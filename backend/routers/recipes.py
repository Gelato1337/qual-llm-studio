"""Recipe CRUD endpoints.

Routes:
  GET    /api/recipes                 — list all recipes (name, description, tier, tags)
  GET    /api/recipes/{name}          — get one recipe as JSON dict + text rendering
  POST   /api/recipes                 — save a new recipe (from JSON dict)
  PUT    /api/recipes/{name}          — update recipe (from JSON dict)
  DELETE /api/recipes/{name}          — delete recipe file

Recipes live in recipes/custom/ (and subfolders) on disk.
The list endpoint scans the full recipes/ tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import paths
from app.recipe import Recipe, RecipeError, list_recipes, load_recipe, save_recipe

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recipe_stub(r: Recipe, path: Path) -> dict:
    return {
        "name": r.name,
        "description": r.description,
        "type": r.type,
        "tier": r.tier,
        "tags": r.tags,
        "path": str(path.relative_to(paths.RECIPES_DIR)),
    }


def _find_recipe_path(name: str) -> Path | None:
    """Find the file path of a recipe by name."""
    if not paths.RECIPES_DIR.exists():
        return None
    for p in paths.RECIPES_DIR.rglob("*.json"):
        try:
            r = load_recipe(p)
            if r.name == name:
                return p
        except RecipeError:
            continue
    return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
def list_all_recipes():
    """List all valid recipes found in the recipes/ tree."""
    if not paths.RECIPES_DIR.exists():
        return {"recipes": []}
    out = []
    for p in sorted(paths.RECIPES_DIR.rglob("*.json")):
        try:
            r = load_recipe(p)
            out.append(_recipe_stub(r, p))
        except RecipeError:
            pass
    return {"recipes": out}


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


@router.get("/{name}")
def get_recipe(name: str):
    """Get a recipe by name. Returns JSON dict + human-readable text rendering."""
    path = _find_recipe_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Recipe {name!r} not found")
    try:
        recipe = load_recipe(path)
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "recipe": recipe.to_dict(),
        "text": recipe.render_as_text(),
        "path": str(path.relative_to(paths.RECIPES_DIR)),
    }


# ---------------------------------------------------------------------------
# Save / Update
# ---------------------------------------------------------------------------


class RecipeSaveRequest(BaseModel):
    recipe: dict[str, Any]
    # Optional: sub-folder within recipes/custom/. Defaults to "".
    subfolder: str = ""


def _save_and_respond(recipe_dict: dict, subfolder: str) -> dict:
    try:
        recipe = Recipe.from_dict(recipe_dict)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse recipe: {exc}")

    errors = recipe.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    custom_root = paths.RECIPES_DIR / "custom"
    if subfolder:
        save_dir = custom_root / subfolder
    else:
        save_dir = custom_root

    from app.paths import safe_filename
    filename = safe_filename(recipe.name) + ".json"
    dest = save_dir / filename

    try:
        save_recipe(recipe, dest)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "saved": True,
        "name": recipe.name,
        "path": str(dest.relative_to(paths.RECIPES_DIR)),
    }


@router.post("")
def create_recipe(req: RecipeSaveRequest):
    """Save a new recipe. Overwrites if same name already exists."""
    return _save_and_respond(req.recipe, req.subfolder)


@router.put("/{name}")
def update_recipe(name: str, req: RecipeSaveRequest):
    """Update an existing recipe. Creates it if it doesn't exist yet."""
    return _save_and_respond(req.recipe, req.subfolder)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{name}")
def delete_recipe(name: str):
    """Delete a recipe file by name."""
    path = _find_recipe_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Recipe {name!r} not found")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"deleted": True, "name": name}
