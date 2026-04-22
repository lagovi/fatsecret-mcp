"""MCP tool surface (FastMCP, stdio transport).

Tools wrap the FS REST API with intuitive semantics — all the FS quirks
captured in notes here are transparently handled so callers don't trip on
them.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Client, FatSecretError
from .config import Config

EPOCH = _dt.date(1970, 1, 1)

# FS-valid meal values. App also has "Snack" in its UI but the API rejects it;
# snack entries must be logged as "Other". We normalize.
MEAL_NORMALIZE = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "other": "Other",
    "snack": "Other",
    "snacks": "Other",
}


def build_server() -> FastMCP:
    cfg = Config.load()
    if cfg.user_token is None:
        raise RuntimeError(
            "No user token configured — the diary tools need 3-legged OAuth. "
            "Run `fatsecret-mcp auth` once to authorize, then re-start the server."
        )
    client = Client(consumer=cfg.consumer, token=cfg.user_token)
    mcp = FastMCP("fatsecret")
    _register_tools(mcp, client)
    return mcp


def _date_int(date_str: str = "") -> int:
    d = _dt.date.fromisoformat(date_str) if date_str else _dt.date.today()
    return (d - EPOCH).days


def _register_tools(mcp: FastMCP, client: Client) -> None:
    # ---- public food DB ----------------------------------------------------

    @mcp.tool()
    def search_food(query: str, max_results: int = 10) -> str:
        """Search FatSecret's public food database by name/brand."""
        max_results = max(1, min(50, int(max_results)))
        res = client.call("foods.search", {"search_expression": query, "max_results": str(max_results)})
        foods = (res.get("foods") or {}).get("food") or []
        if isinstance(foods, dict):
            foods = [foods]
        if not foods:
            return f"no results for: {query}"
        lines = []
        for f in foods:
            tag = f" [{f['brand_name']}]" if f.get("brand_name") else ""
            lines.append(f"- [{f.get('food_id')}] {f.get('food_name')}{tag}  {f.get('food_description', '')}")
        return "\n".join(lines)

    @mcp.tool()
    def get_food(food_id: str) -> str:
        """Full macros + every available serving (with serving_id) for a food."""
        res = client.call("food.get.v4", {"food_id": str(food_id)})
        food = res.get("food")
        if not food:
            return f"food not found: {food_id}"
        name = food.get("food_name", "")
        brand = food.get("brand_name", "")
        servings = (food.get("servings") or {}).get("serving") or []
        if isinstance(servings, dict):
            servings = [servings]
        header = f"{name}" + (f" [{brand}]" if brand else "")
        lines = [header]
        for s in servings[:20]:
            lines.append(
                f"  [serving_id {s.get('serving_id')}] {s.get('serving_description', '')}: "
                f"{s.get('calories', '?')} cal, P{s.get('protein', '?')} F{s.get('fat', '?')} C{s.get('carbohydrate', '?')}"
            )
        return "\n".join(lines)

    # ---- user diary --------------------------------------------------------

    @mcp.tool()
    def get_profile() -> str:
        """Get the authenticated user's FS profile (height, weight, goal)."""
        res = client.call("profile.get")
        return json.dumps(res.get("profile", {}), indent=2)

    @mcp.tool()
    def get_diary(date: str = "") -> str:
        """Diary entries for a date (YYYY-MM-DD, default today), grouped by meal."""
        d_int = _date_int(date)
        res = client.call("food_entries.get.v2", {"date": str(d_int)})
        entries = (res.get("food_entries") or {}).get("food_entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        if not entries:
            return f"no entries for {date or 'today'}"
        by_meal: dict[str, list[str]] = {}
        totals = {"cal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
        for e in entries:
            meal = e.get("meal", "Other")
            cal = float(e.get("calories", 0) or 0)
            p = float(e.get("protein", 0) or 0)
            f_ = float(e.get("fat", 0) or 0)
            c = float(e.get("carbohydrate", 0) or 0)
            totals["cal"] += cal; totals["p"] += p; totals["f"] += f_; totals["c"] += c
            by_meal.setdefault(meal, []).append(
                f"  [{e.get('food_entry_id')}] {e.get('food_entry_name')} — "
                f"{cal:.0f} cal, P{p:.1f} F{f_:.1f} C{c:.1f}"
            )
        out = [f"Diary {date or 'today'}:"]
        for meal in ("Breakfast", "Lunch", "Dinner", "Other"):
            if meal in by_meal:
                out.append(f"{meal}:")
                out.extend(by_meal[meal])
        out.append(f"TOTAL: {totals['cal']:.0f} cal, P{totals['p']:.1f} F{totals['f']:.1f} C{totals['c']:.1f}")
        return "\n".join(out)

    @mcp.tool()
    def log_food(
        food_id: str,
        serving_id: str,
        servings: float,
        meal: str = "Breakfast",
        date: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Log a food to the user's diary.

        `servings` is an intuitive multiplier of the named serving — e.g.
        2 for "2 tbsp", 0.5 for "half a stick". The MCP translates that to
        FS's `number_of_units` semantics internally.

        FS gotcha (handled here): the API's `number_of_units` is in the
        serving's own measurement units (grams for a "100 g" serving, tbsp
        for "1 tbsp", etc.), NOT a multiplier. Each serving carries its own
        `number_of_units` describing how many measurement-units equal one
        whole serving. We multiply caller's `servings` by that to produce
        the correct API value.

        Meal: Breakfast | Lunch | Dinner | Other. FS rejects "Snack" — we
        map it to "Other" automatically.
        """
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")

        info = client.call("food.get.v4", {"food_id": str(food_id)}).get("food") or {}
        if not food_entry_name:
            food_entry_name = info.get("food_name") or f"food {food_id}"
        servings_list = (info.get("servings") or {}).get("serving") or []
        if isinstance(servings_list, dict):
            servings_list = [servings_list]
        serving = next((s for s in servings_list if str(s.get("serving_id")) == str(serving_id)), None)
        if not serving:
            raise RuntimeError(f"serving_id {serving_id} not found on food {food_id}")
        serving_units = float(serving.get("number_of_units") or 1)
        serving_desc = serving.get("serving_description") or "?"
        api_units = float(servings) * serving_units

        res = client.call("food_entry.create", {
            "food_id": str(food_id),
            "food_entry_name": food_entry_name,
            "serving_id": str(serving_id),
            "number_of_units": f"{api_units:.4f}".rstrip("0").rstrip("."),
            "meal": meal_key,
            "date": str(_date_int(date)),
        })
        fe = res.get("food_entry_id")
        fe_id = fe.get("value") if isinstance(fe, dict) else fe
        if not fe_id:
            raise RuntimeError(f"FS returned no food_entry_id — unexpected response: {res}")
        return (
            f"logged (food_entry_id={fe_id}) {servings}× '{serving_desc}' of "
            f"{food_entry_name} to {meal_key} on {date or 'today'} "
            f"(sent number_of_units={api_units})"
        )

    @mcp.tool()
    def delete_entry(food_entry_id: str) -> str:
        """Delete a diary entry by food_entry_id (from get_diary)."""
        client.call("food_entry.delete", {"food_entry_id": str(food_entry_id)})
        return f"deleted entry {food_entry_id}"

    @mcp.tool()
    def create_custom_food(
        name: str, brand: str = "",
        calories: float = 0, protein: float = 0, fat: float = 0, carbs: float = 0,
    ) -> str:
        """Create a custom food with per-100g macros.

        PREMIER-ONLY on FatSecret's platform tier. Free-tier apps will
        receive 'invalid_scope' or similar. Upgrade the app in the FS dev
        console if you need custom foods.
        """
        try:
            res = client.call("foods.create", {
                "food_name": name,
                "brand_name": brand or "",
                "calories": str(calories),
                "protein": str(protein),
                "fat": str(fat),
                "carbohydrate": str(carbs),
            })
        except FatSecretError as e:
            if "scope" in e.message.lower() or "premier" in e.message.lower():
                return f"create_custom_food requires FS premier tier. Current error: {e}"
            raise
        return json.dumps(res, indent=2)
