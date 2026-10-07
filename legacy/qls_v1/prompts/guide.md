# qls command reference (for agents and researchers)

The project folder is found from the current directory, `--project PATH`, or `$QLS_PROJECT`.
The run is `--run NAME` or `$QLS_RUN`. Your identity in the decision log is `--actor` or `$QLS_ACTOR`
(use `human` only when recording a decision the researcher made).

Add `--json` to any read command for machine-readable output.

## Read the data
    qls docs                                   list transcripts (id, participant, turns, segments)
    qls doc read P01 [--from 12] [--limit 40]  read turns of a transcript, with segment IDs
    qls search "vendor lock" [--doc P01] [--regex] [--all-turns]
                                               find text in informant segments (or all turns)

## Rebuild context before deciding
    qls context c12 c40 t3 [--max-chars 12000] [--quotes 3]
                                               definitions, coding memos (through merges), memos, and quotes
                                               inside the conversation they came from, for any IDs
    qls memos [--about c12] [--kind counter]  memos about an item (concepts include their merge history)

## Inspect a run
    qls runs                                   list runs
    qls status                                 counts, unassigned concepts, flags
    qls structure [--no-concepts]              dimension > theme > concept tree
    qls concepts [--unassigned] [--theme t3] [--cards] [--quotes 3]
    qls concept show c12                       full card: description, informants, quotes in context
    qls decisions [--last 20]                  the decision log
    qls check                                  integrity: nothing lost, no double assignment

## Change the analysis (each needs --reason "one line"; add --evidence <segment/quote IDs> you relied on)
    qls concept merge c3 c9 c14 --label "..." --description "..." --reason "..."
    qls concept split c7 --part "label A|q12,q13" --part "label B|q14" --reason "..."
    qls concept rename c7 --label "..." [--description "..."] --reason "..."
    qls concept drop c7 --reason "..."         (qls concept restore c7 --reason "...")
    qls theme create --label "..." --definition "..." --concepts c1 c4 c9 --reason "..."
    qls theme assign t2 c11 c12 --reason "..."  (moves concepts from any other theme)
    qls theme unassign c11 --reason "..."
    qls theme rename t2 --label "..." [--definition "..."] --reason "..."
    qls theme drop t2 --reason "..."           (its concepts become unassigned)
    qls dim create --label "..." --definition "..." --themes t1 t2 --reason "..."
    qls dim assign a1 t5 --reason "..."
    qls dim rename a1 --label "..." --reason "..."
    qls dim drop a1 --reason "..."
    qls memo "free text" [--link c12 --link t3] [--kind boundary|surprise|counter|alternative|decision|summary]
              [--evidence P04:s012 q33]

## Runs, comparison, reports
    qls fork SRC NEW [--blind] [--exclude-doc P04]
                                               copy a run; --blind keeps only concepts (independent grouping);
                                               --exclude-doc drops a transcript's quotes (leave-one-out)
    qls compare RUN_A RUN_B [--html out.html]  structural agreement (Rand, ARI, NMI) and differing groups
    qls consensus RUN1 RUN2 RUN3 ...           stability of groupings across runs
    qls report [--out file.html]               HTML report of a run
    qls export                                 CSV tables of a run into exports/<run>/

## Pipeline stages (call a model; need an API key or local endpoint)
    qls code [--run NAME] [--doc P01] [--passes 3]   stage 1: 1st-order concepts with verified quotes
    qls consolidate RUN [--into NEW] [--no-model]    stage 2: merge identical / same-meaning concepts
    qls analyst --from RUN [--replicates 5]          stages 3-4 by independent pi agents
    qls group RUN [--view labels|cards|memos] [--no-verify]
                                                     stages 3-4 by a fixed prompt; --view sets what the model sees,
                                                     verify = return to data after each abstraction
    qls oneshot [--run NAME]                         whole corpus in one call (long-context baseline)
    qls metrics RUN [RUN ...]                        genericness, informant voices, coverage
