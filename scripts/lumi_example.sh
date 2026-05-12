#!/bin/bash
#SBATCH --job-name=QualLLMStudio
#SBATCH --account=project_462000999
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --gpus-per-node=8
#SBATCH --mem=400G
#SBATCH --time=04:00:00

# Adapted from the elephant-pipeline LUMI script.
# Starts Ollama, then launches the Gradio UI pointed at it.
# Reach the UI from your laptop via SSH port-forwarding:
#   ssh -L 7860:NODE:7860 lumi
# Then open http://localhost:7860 in a local browser.

module purge
module use /appl/local/csc/modulefiles
module load pytorch

# --- adjust these paths to your setup ---
OLLAMA_BASE=/pfs/lustrep4/scratch/project_462000999/$USER/ollama_test
APP_DIR=/pfs/lustrep4/scratch/project_462000999/$USER/qual-llm-studio
# ----------------------------------------

cd "$OLLAMA_BASE"
export OLLAMA_MODELS="$OLLAMA_BASE/models"
export PATH="$OLLAMA_BASE/bin:$PATH"

# Start Ollama with the retry-until-GPUs-discovered loop.
# (Same pattern as the elephant pipeline — needed because GPU discovery
# is racy on LUMI compute nodes.)
MAX_ATTEMPTS=5
OLLAMA_PID=""
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo "=== Ollama start attempt $attempt/$MAX_ATTEMPTS ==="
    srun --overlap ollama serve &
    OLLAMA_PID=$!

    READY=0
    for i in $(seq 1 90); do
        if curl -s http://localhost:11434/ > /dev/null 2>&1; then
            READY=1
            echo "Ollama responding after ${i}s"
            break
        fi
        sleep 1
    done

    if [ $READY -eq 0 ]; then
        echo "Ollama never responded, killing and retrying..."
        kill $OLLAMA_PID 2>/dev/null
        wait $OLLAMA_PID 2>/dev/null
        sleep 5
        continue
    fi

    # Quick GPU sanity check: load a tiny model and confirm VRAM is used.
    sleep 5
    curl -s http://localhost:11434/api/generate -d '{
        "model": "llama3:latest",
        "prompt": "hi",
        "stream": false,
        "options": {"num_predict": 1}
    }' --max-time 120 > /dev/null 2>&1

    PS_OUT=$(curl -s http://localhost:11434/api/ps 2>/dev/null)
    if echo "$PS_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if any(m.get('size_vram',0)>0 for m in d.get('models',[])) else 1)"; then
        echo "✓ GPUs discovered on attempt $attempt"
        break
    else
        echo "✗ No GPU detected, retrying..."
        kill $OLLAMA_PID 2>/dev/null
        wait $OLLAMA_PID 2>/dev/null
        sleep 10
        if [ $attempt -eq $MAX_ATTEMPTS ]; then
            echo "FATAL: GPU discovery failed after $MAX_ATTEMPTS attempts"
            exit 1
        fi
    fi
done

ollama list

# Launch the Gradio UI. It'll bind on 0.0.0.0:7860 — reach it via SSH tunnel.
cd "$APP_DIR"
python3 -m app.main --host 0.0.0.0 --port 7860

kill $OLLAMA_PID 2>/dev/null
wait $OLLAMA_PID 2>/dev/null
