"""MCP tool surface (FastMCP, stdio transport).

Tools wrap the FS REST API with intuitive semantics — all the FS quirks
captured in notes here are transparently handled so callers don't trip on
them.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import pathlib
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Client, FatSecretError
from .config import Config

EPOCH = _dt.date(1970, 1, 1)
MAX_DIARY_RANGE_DAYS = 31

_MACRO_FIELDS = ("calories", "protein", "fat", "carbohydrate")
_NUTRIENT_FIELDS = (
    *_MACRO_FIELDS,
    "saturated_fat",
    "polyunsaturated_fat",
    "monounsaturated_fat",
    "cholesterol",
    "sodium",
    "potassium",
    "fiber",
    "sugar",
    "vitamin_a",
    "vitamin_c",
    "calcium",
    "iron",
)

_CUSTOM_FOOD_OPTIONAL_NUTRIENTS = (
    "calories_from_fat",
    "saturated_fat",
    "polyunsaturated_fat",
    "monounsaturated_fat",
    "trans_fat",
    "cholesterol",
    "sodium",
    "potassium",
    "fiber",
    "sugar",
    "added_sugars",
    "vitamin_d",
    "vitamin_a",
    "vitamin_c",
    "calcium",
    "iron",
)
_CUSTOM_FOOD_BRAND_TYPES = {"manufacturer", "restaurant", "supermarket"}
_CUSTOM_FOOD_SERVING_UNITS = {"g", "ml", "oz"}

MEAL_NORMALIZE = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "other": "Other",
    "snack": "Other",
    "snacks": "Other",
}

_WEIGHT_TO_G = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "oz": 28.3495,
    "ounce": 28.3495,
    "ounces": 28.3495,
    "lb": 453.592,
    "lbs": 453.592,
    "pound": 453.592,
    "pounds": 453.592,
    "kg": 1000.0,
}
_VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "floz": 29.5735,
    "fl_oz": 29.5735,
    "fluid_ounce": 29.5735,
    "tbsp": 14.7868,
    "tablespoon": 14.7868,
    "tablespoons": 14.7868,
    "tsp": 4.92892,
    "teaspoon": 4.92892,
    "teaspoons": 4.92892,
    "cup": 236.588,
    "cups": 236.588,
}


def _local_custom_foods_path() -> pathlib.Path:
    base = pathlib.Path.home() / ".config" / "fatsecret-mcp"
    base.mkdir(parents=True, exist_ok=True)
    return base / "custom_foods.json"


def _load_local_custom_foods() -> list[dict[str, Any]]:
    p = _local_custom_foods_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return []


def _save_local_custom_foods(foods: list[dict[str, Any]]) -> None:
    p = _local_custom_foods_path()
    p.write_text(json.dumps(foods, indent=2, ensure_ascii=False))


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


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise RuntimeError(f"unexpected FatSecret list value: {value!r}")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nutrient_totals(nutrient_sets: list[dict[str, float | None]]) -> dict[str, float | None]:
    totals: dict[str, float | None] = {}
    for field in _NUTRIENT_FIELDS:
        values = [nutrients[field] for nutrients in nutrient_sets if nutrients[field] is not None]
        totals[field] = sum(values) if values else (0.0 if field in _MACRO_FIELDS else None)
    return totals


def _raw_or_cooked(*descriptions: Any) -> str | None:
    for description in descriptions:
        match = re.search(r"\b(raw|cooked|cooking)\b", str(description or ""), re.IGNORECASE)
        if match:
            return "raw" if match.group(1).lower() == "raw" else "cooked"
    return None


def _diary_entries(client: Client, date: _dt.date) -> list[dict[str, Any]]:
    try:
        res = client.call("food_entries.get.v2", {"date": str((date - EPOCH).days)})
    except FatSecretError as e:
        if e.code == 1:
            return []
        raise
    return _as_list((res.get("food_entries") or {}).get("food_entry"))


def _serving_for_entry(
    client: Client,
    entry: dict[str, Any],
    food_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    food_id = str(entry.get("food_id") or "")
    if not food_id or food_id.startswith("custom:"):
        return {}
    if food_id not in food_cache:
        try:
            food_cache[food_id] = client.call("food.get.v4", {"food_id": food_id}).get("food") or {}
        except FatSecretError:
            food_cache[food_id] = {}
    servings = _as_list((food_cache[food_id].get("servings") or {}).get("serving"))
    serving_id = str(entry.get("serving_id") or "")
    return next((s for s in servings if str(s.get("serving_id")) == serving_id), {})


def _enrich_diary_entry(
    client: Client,
    entry: dict[str, Any],
    food_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    serving = _serving_for_entry(client, entry, food_cache)
    entry_units = _number(entry.get("number_of_units"))
    serving_units = _number(serving.get("number_of_units"))
    metric_serving_amount = serving.get("metric_serving_amount")
    metric_amount = None
    if metric_serving_amount not in (None, "") and serving_units > 0:
        metric_amount = _number(metric_serving_amount) * entry_units / serving_units

    measurement = serving.get("measurement_description")
    nutrients = {field: _optional_number(entry.get(field)) for field in _NUTRIENT_FIELDS}
    macros = {field: nutrients[field] for field in _MACRO_FIELDS}
    return {
        "food_entry_id": str(entry.get("food_entry_id") or ""),
        "date": (EPOCH + _dt.timedelta(days=int(entry.get("date_int") or 0))).isoformat(),
        "meal": entry.get("meal") or "Other",
        "food_id": str(entry.get("food_id") or ""),
        "serving_id": str(entry.get("serving_id") or ""),
        "number_of_units": entry_units,
        "original_amount": entry_units,
        "original_unit": measurement,
        "food_entry_description": entry.get("food_entry_description"),
        "serving_description": serving.get("serving_description"),
        "measurement_description": measurement,
        "metric_serving_amount": (
            _number(metric_serving_amount) if metric_serving_amount not in (None, "") else None
        ),
        "metric_serving_unit": serving.get("metric_serving_unit"),
        "metric_amount": metric_amount,
        "raw_or_cooked": _raw_or_cooked(
            measurement,
            serving.get("serving_description"),
            entry.get("food_entry_description"),
            entry.get("food_entry_name"),
        ),
        "food_entry_name": entry.get("food_entry_name") or "",
        **nutrients,
        "macros": macros,
        "nutrients": nutrients,
    }


def _day_diary(
    client: Client,
    date: _dt.date,
    food_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    food_cache = food_cache if food_cache is not None else {}
    entries = [_enrich_diary_entry(client, entry, food_cache) for entry in _diary_entries(client, date)]
    totals = _nutrient_totals([entry["nutrients"] for entry in entries])
    return {"date": date.isoformat(), "entries": entries, "totals": totals}


def _diary_range(client: Client, start: _dt.date, end: _dt.date) -> dict[str, Any]:
    if end < start:
        raise RuntimeError("end_date must be on or after start_date")
    day_count = (end - start).days + 1
    if day_count > MAX_DIARY_RANGE_DAYS:
        raise RuntimeError(f"date range may not exceed {MAX_DIARY_RANGE_DAYS} days")

    food_cache: dict[str, dict[str, Any]] = {}
    days = [
        _day_diary(client, start + _dt.timedelta(days=offset), food_cache)
        for offset in range(day_count)
    ]
    totals = _nutrient_totals([day["totals"] for day in days])
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": days,
        "totals": totals,
    }


def _replace_entry(
    client: Client,
    food_entry_id: str,
    serving_id: str,
    number_of_units: float,
    meal: str = "",
    food_entry_name: str = "",
) -> dict[str, Any]:
    units = float(number_of_units)
    if not math.isfinite(units) or units <= 0:
        raise RuntimeError("number_of_units must be a positive finite number")

    params = {
        "food_entry_id": str(food_entry_id),
        "serving_id": str(serving_id),
        "number_of_units": f"{units:.4f}".rstrip("0").rstrip("."),
    }
    if meal:
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")
        params["meal"] = meal_key
    if food_entry_name:
        params["food_entry_name"] = food_entry_name

    res = client.call("food_entry.edit", params)
    success = res.get("success")
    success_value = success.get("value") if isinstance(success, dict) else success
    if str(success_value) != "1":
        raise RuntimeError(f"FS did not confirm food_entry.edit success: {res}")
    return {
        "replaced": True,
        "food_entry_id": str(food_entry_id),
        "serving_id": str(serving_id),
        "number_of_units": units,
        **({"meal": params["meal"]} if "meal" in params else {}),
        **({"food_entry_name": food_entry_name} if food_entry_name else {}),
    }


def _custom_food_decimal(value: Any, field: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"{field} must be a non-negative finite number") from None
    if not math.isfinite(number) or number < 0:
        raise RuntimeError(f"{field} must be a non-negative finite number")
    return f"{number:.6f}".rstrip("0").rstrip(".") or "0"


def _create_custom_food(
    client: Client,
    *,
    name: str,
    brand: str = "",
    calories: float = 0,
    protein: float = 0,
    fat: float = 0,
    carbs: float = 0,
    serving_size: str = "1 serving",
    serving_amount: float | None = None,
    serving_amount_unit: str = "g",
    brand_type: str = "manufacturer",
    calories_from_fat: float | None = None,
    saturated_fat: float | None = None,
    polyunsaturated_fat: float | None = None,
    monounsaturated_fat: float | None = None,
    trans_fat: float | None = None,
    cholesterol: float | None = None,
    sodium: float | None = None,
    potassium: float | None = None,
    fiber: float | None = None,
    sugar: float | None = None,
    added_sugars: float | None = None,
    vitamin_d: float | None = None,
    vitamin_a: float | None = None,
    vitamin_c: float | None = None,
    calcium: float | None = None,
    iron: float | None = None,
) -> dict[str, Any]:
    food_name = name.strip()
    if not food_name:
        raise RuntimeError("name must not be blank")
    serving_description = serving_size.strip()
    if not serving_description:
        raise RuntimeError("serving_size must not be blank")

    normalized_brand_type = brand_type.lower().strip()
    if normalized_brand_type not in _CUSTOM_FOOD_BRAND_TYPES:
        allowed = ", ".join(sorted(_CUSTOM_FOOD_BRAND_TYPES))
        raise RuntimeError(f"brand_type must be one of: {allowed}")

    params = {
        "brand_type": normalized_brand_type,
        "food_name": food_name,
        "serving_size": serving_description,
        "calories": _custom_food_decimal(calories, "calories"),
        "fat": _custom_food_decimal(fat, "fat"),
        "carbohydrate": _custom_food_decimal(carbs, "carbs"),
        "protein": _custom_food_decimal(protein, "protein"),
    }
    brand_name = brand.strip()
    if brand_name:
        params["brand_name"] = brand_name

    if serving_amount is not None:
        try:
            amount = float(serving_amount)
        except (TypeError, ValueError):
            raise RuntimeError("serving_amount must be a positive finite number") from None
        if not math.isfinite(amount) or amount <= 0:
            raise RuntimeError("serving_amount must be a positive finite number")
        normalized_unit = serving_amount_unit.lower().strip()
        if normalized_unit not in _CUSTOM_FOOD_SERVING_UNITS:
            allowed = ", ".join(sorted(_CUSTOM_FOOD_SERVING_UNITS))
            raise RuntimeError(f"serving_amount_unit must be one of: {allowed}")
        params["serving_amount"] = f"{amount:.6f}".rstrip("0").rstrip(".")
        params["serving_amount_unit"] = normalized_unit

    optional_nutrients = {
        "calories_from_fat": calories_from_fat,
        "saturated_fat": saturated_fat,
        "polyunsaturated_fat": polyunsaturated_fat,
        "monounsaturated_fat": monounsaturated_fat,
        "trans_fat": trans_fat,
        "cholesterol": cholesterol,
        "sodium": sodium,
        "potassium": potassium,
        "fiber": fiber,
        "sugar": sugar,
        "added_sugars": added_sugars,
        "vitamin_d": vitamin_d,
        "vitamin_a": vitamin_a,
        "vitamin_c": vitamin_c,
        "calcium": calcium,
        "iron": iron,
    }
    for field in _CUSTOM_FOOD_OPTIONAL_NUTRIENTS:
        value = optional_nutrients[field]
        if value is not None:
            params[field] = _custom_food_decimal(value, field)

    try:
        res = client.call("food.create.v2", params)
    except FatSecretError as e:
        return {
            "created": False,
            "error": "premier_required",
            "message": (
                "FatSecret custom-food creation is Premier Exclusive, and this "
                "developer app does not have access to food.create.v2. Upgrade the "
                "app's FatSecret Platform edition, then retry the same request."
            ),
            "fatsecret_error": {"code": e.code, "message": e.message},
        }

    food_id = res.get("food_id")
    food_id = food_id.get("value") if isinstance(food_id, dict) else food_id
    if not food_id:
        raise RuntimeError(f"FS returned no food_id — unexpected response: {res}")
    return {
        "created": True,
        "food_id": str(food_id),
        "food_name": food_name,
        "brand_name": brand_name or None,
        "serving_size": serving_description,
    }


def _register_tools(mcp: FastMCP, client: Client) -> None:
    # ---- public food DB & local custom db ----------------------------------

    @mcp.tool()
    def search_food(query: str, max_results: int = 10) -> str:
        """Search food database by name/brand (includes local custom products)."""
        max_results = max(1, min(50, int(max_results)))
        lines = []

        local_foods = _load_local_custom_foods()
        query_lower = query.lower()
        matched_local = [
            f for f in local_foods
            if query_lower in f.get("name", "").lower() or query_lower in f.get("brand", "").lower()
        ]
        for f in matched_local[:max_results]:
            brand_tag = f" [{f['brand']}]" if f.get("brand") else ""
            desc = f"Per {f.get('serving_size', '100g')} - Calories: {f.get('calories', 0)}kcal | Fat: {f.get('fat', 0)}g | Carbs: {f.get('carbs', 0)}g | Protein: {f.get('protein', 0)}g"
            lines.append(f"- [custom:{f.get('id')}] (LOCAL) {f.get('name')}{brand_tag}  {desc}")

        remaining_slots = max_results - len(lines)
        if remaining_slots > 0:
            res = client.call("foods.search", {"search_expression": query, "max_results": str(remaining_slots)})
            foods = (res.get("foods") or {}).get("food") or []
            if isinstance(foods, dict):
                foods = [foods]
            for f in foods:
                tag = f" [{f['brand_name']}]" if f.get("brand_name") else ""
                lines.append(f"- [{f.get('food_id')}] {f.get('food_name')}{tag}  {f.get('food_description', '')}")

        if not lines:
            return f"no results for: {query}"
        return "\n".join(lines)

    @mcp.tool()
    def get_food(food_id: str) -> str:
        """Full macros + every available serving for a food."""
        if str(food_id).startswith("custom:"):
            cid = str(food_id).replace("custom:", "")
            local_foods = _load_local_custom_foods()
            found = next((f for f in local_foods if str(f.get("id")) == cid), None)
            if not found:
                return f"local custom food not found: {food_id}"
            header = f"(LOCAL) {found.get('name', '')}" + (f" [{found.get('brand')}]" if found.get('brand') else "")
            lines = [header, f"  [serving_id local_1] {found.get('serving_size', '100g')}: {found.get('calories', 0)} cal, P{found.get('protein', 0)} F{found.get('fat', 0)} C{found.get('carbs', 0)}"]
            return "\n".join(lines)

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

    # ---- Saved Meals (Favorite Meals in FatSecret UI) ----------------------

    @mcp.tool()
    def get_saved_meals() -> str:
        """Get all saved meals (Favorite Meals in FatSecret web UI)."""
        res = client.call("saved_meals.get.v2", {})
        meals = (res.get("saved_meals") or {}).get("saved_meal") or []
        if isinstance(meals, dict):
            meals = [meals]
        if not meals:
            return "no saved meals found"
        lines = ["=== Saved Meals (Favorite Meals) ==="]
        for m in meals:
            lines.append(f"- [saved_meal_id: {m.get('saved_meal_id')}] {m.get('saved_meal_name')} (Suitable for: {m.get('meals', '')})")
        return "\n".join(lines)

    @mcp.tool()
    def get_saved_meal_details(saved_meal_id: str) -> str:
        """Get ingredient breakdown of a saved meal by saved_meal_id."""
        res = client.call("saved_meal_items.get.v2", {"saved_meal_id": str(saved_meal_id)})
        items = (res.get("saved_meal_items") or {}).get("saved_meal_item") or []
        if isinstance(items, dict):
            items = [items]
        if not items:
            return f"no items in saved meal {saved_meal_id}"
        lines = [f"=== Items in Saved Meal {saved_meal_id} ==="]
        for item in items:
            lines.append(f"- [{item.get('food_id')}] {item.get('saved_meal_item_name')}: {item.get('number_of_units')} units (serving_id: {item.get('serving_id')})")
        return "\n".join(lines)

    @mcp.tool()
    def log_saved_meal(saved_meal_id: str, meal: str = "Breakfast", date: str = "") -> str:
        """Log an entire saved meal (Favorite Meal) to the user's diary."""
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other.")
        params = {
            "saved_meal_id": str(saved_meal_id),
            "meal": meal_key,
        }
        if date:
            params["date"] = str(_date_int(date))
        res = client.call("food_entries.copy_saved_meal", params)
        success = res.get("success")
        val = success.get("value") if isinstance(success, dict) else success
        if str(val) != "1":
            raise RuntimeError(f"Failed to copy saved meal {saved_meal_id}: {res}")
        return f"successfully logged saved_meal_id {saved_meal_id} to {meal_key} on {date or 'today'}"

    # ---- Favorites (Starred Foods) -----------------------------------------

    @mcp.tool()
    def get_favorites() -> str:
        """Get all favorite (starred) foods for the user."""
        res = client.call("foods.get_favorites.v2", {})
        foods = (res.get("foods") or {}).get("food") or []
        if isinstance(foods, dict):
            foods = [foods]
        if not foods:
            return "no favorite foods found"
        lines = ["=== Favorite Foods ==="]
        for f in foods:
            lines.append(f"- [{f.get('food_id')}] {f.get('food_name')} (serving_id: {f.get('serving_id')}, units: {f.get('number_of_units')})")
        return "\n".join(lines)

    @mcp.tool()
    def add_favorite(food_id: str, serving_id: str = "", number_of_units: float = 1.0) -> str:
        """Add a food item to user's favorites."""
        params = {"food_id": str(food_id)}
        if serving_id:
            params["serving_id"] = str(serving_id)
            params["number_of_units"] = str(number_of_units)
        res = client.call("food.add_favorite", params)
        return f"added food_id {food_id} to favorites"

    @mcp.tool()
    def delete_favorite(food_id: str, serving_id: str = "", number_of_units: float = 1.0) -> str:
        """Delete a food item from user's favorites."""
        params = {"food_id": str(food_id)}
        if serving_id:
            params["serving_id"] = str(serving_id)
            params["number_of_units"] = str(number_of_units)
        res = client.call("food.delete_favorite", params)
        return f"removed food_id {food_id} from favorites"

    # ---- Local Custom Foods Management -----------------------------------

    @mcp.tool()
    def add_custom_food_local(
        name: str,
        brand: str = "",
        calories: float = 0,
        protein: float = 0,
        fat: float = 0,
        carbs: float = 0,
        serving_size: str = "100g",
    ) -> str:
        """Add a custom product to local JSON database (~/.config/fatsecret-mcp/custom_foods.json)."""
        foods = _load_local_custom_foods()
        new_id = str(len(foods) + 1)
        item = {
            "id": new_id,
            "name": name.strip(),
            "brand": brand.strip(),
            "calories": float(calories),
            "protein": float(protein),
            "fat": float(fat),
            "carbs": float(carbs),
            "serving_size": serving_size.strip(),
        }
        foods.append(item)
        _save_local_custom_foods(foods)
        return f"saved local custom food [custom:{new_id}] {name} ({serving_size}: {calories} cal, P{protein} F{fat} C{carbs})"

    @mcp.tool()
    def get_custom_foods_local() -> str:
        """List all custom products stored in local JSON database."""
        foods = _load_local_custom_foods()
        if not foods:
            return "no local custom foods stored"
        lines = ["=== Local Custom Foods ==="]
        for f in foods:
            brand_tag = f" [{f['brand']}]" if f.get("brand") else ""
            lines.append(f"- [custom:{f.get('id')}] {f.get('name')}{brand_tag} ({f.get('serving_size')}): {f.get('calories')} cal, P{f.get('protein')} F{f.get('fat')} C{f.get('carbs')}")
        return "\n".join(lines)

    @mcp.tool()
    def delete_custom_food_local(custom_id: str) -> str:
        """Delete a local custom product by ID (e.g. '1' or 'custom:1')."""
        cid = str(custom_id).replace("custom:", "").strip()
        foods = _load_local_custom_foods()
        filtered = [f for f in foods if str(f.get("id")) != cid]
        if len(filtered) == len(foods):
            return f"custom food with id {custom_id} not found"
        _save_local_custom_foods(filtered)
        return f"deleted local custom food id {custom_id}"

    # ---- user diary --------------------------------------------------------

    @mcp.tool()
    def get_profile() -> str:
        """Get the authenticated user's FS profile (height, weight, goal)."""
        res = client.call("profile.get")
        return json.dumps(res.get("profile", {}), indent=2)

    @mcp.tool()
    def get_diary(date: str = "") -> str:
        """Get one day's diary as structured JSON (YYYY-MM-DD, default today)."""
        day = _dt.date.fromisoformat(date) if date else _dt.date.today()
        return json.dumps(_day_diary(client, day), indent=2)

    @mcp.tool()
    def get_diary_range(start_date: str, end_date: str) -> str:
        """Get an inclusive date range of enriched diary entries as JSON."""
        start = _dt.date.fromisoformat(start_date)
        end = _dt.date.fromisoformat(end_date)
        return json.dumps(_diary_range(client, start, end), indent=2)

    @mcp.tool()
    def log_food(
        food_id: str,
        serving_id: str,
        servings: float,
        meal: str = "Breakfast",
        date: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Log a food to the user's diary."""
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
    def log_amount(
        food_id: str,
        amount: float,
        unit: str = "g",
        meal: str = "Breakfast",
        date: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Log a food by absolute amount + unit — no need to pre-pick a serving."""
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")

        unit_norm = unit.lower().strip().replace(" ", "_")
        if unit_norm in _WEIGHT_TO_G:
            amount_g = float(amount) * _WEIGHT_TO_G[unit_norm]
        elif unit_norm in _VOLUME_TO_ML:
            amount_g = float(amount) * _VOLUME_TO_ML[unit_norm]
        else:
            raise RuntimeError(
                f"unknown unit: {unit!r}. Supported: "
                f"{', '.join(sorted(set(_WEIGHT_TO_G) | set(_VOLUME_TO_ML)))}"
            )

        info = client.call("food.get.v4", {"food_id": str(food_id)}).get("food") or {}
        if not food_entry_name:
            food_entry_name = info.get("food_name") or f"food {food_id}"
        servings_list = (info.get("servings") or {}).get("serving") or []
        if isinstance(servings_list, dict):
            servings_list = [servings_list]
        if not servings_list:
            raise RuntimeError(f"food {food_id} has no servings defined")

        named_match = None
        for s in servings_list:
            m = (s.get("measurement_description") or "").lower()
            first_token = m.split(",")[0].split()[0] if m else ""
            if first_token == unit_norm or first_token == unit_norm.rstrip("s"):
                if float(s.get("number_of_units") or 0) == 1.0:
                    named_match = s
                    break
                if named_match is None:
                    named_match = s

        if named_match:
            chosen = named_match
            api_units = float(amount)
            how = f"{amount} {unit_norm}"
        else:
            usable = []
            for s in servings_list:
                mu = (s.get("metric_serving_unit") or "").lower()
                try:
                    msa = float(s.get("metric_serving_amount") or 0)
                    nu = float(s.get("number_of_units") or 0)
                except ValueError:
                    continue
                if msa <= 0 or nu <= 0:
                    continue
                if mu == "g":
                    grams_per_serving_unit = msa / nu
                elif mu == "oz":
                    grams_per_serving_unit = msa * 28.3495 / nu
                elif mu == "ml":
                    grams_per_serving_unit = msa / nu
                else:
                    continue
                usable.append((grams_per_serving_unit, s))
            if not usable:
                raise RuntimeError(
                    f"food {food_id} has no servings with usable metric info. "
                    f"Call get_food({food_id}) + log_food with an explicit serving_id."
                )
            usable.sort()
            grams_per_unit, chosen = usable[0]
            api_units = amount_g / grams_per_unit
            how = (
                f"{amount_g:.2f} g (from {amount} {unit_norm}) → "
                f"{api_units:.3f}× '{chosen.get('serving_description')}'"
            )

        res = client.call("food_entry.create", {
            "food_id": str(food_id),
            "food_entry_name": food_entry_name,
            "serving_id": str(chosen["serving_id"]),
            "number_of_units": f"{api_units:.4f}".rstrip("0").rstrip("."),
            "meal": meal_key,
            "date": str(_date_int(date)),
        })
        fe = res.get("food_entry_id")
        fe_id = fe.get("value") if isinstance(fe, dict) else fe
        if not fe_id:
            raise RuntimeError(f"FS returned no food_entry_id — unexpected response: {res}")
        return (
            f"logged (food_entry_id={fe_id}) {how} of {food_entry_name} "
            f"to {meal_key} on {date or 'today'} "
            f"(via serving '{chosen.get('serving_description')}', number_of_units={api_units})"
        )

    @mcp.tool()
    def replace_entry(
        food_entry_id: str,
        serving_id: str,
        number_of_units: float,
        meal: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Atomically replace an entry's serving and amount via food_entry.edit."""
        return json.dumps(_replace_entry(
            client,
            food_entry_id=food_entry_id,
            serving_id=serving_id,
            number_of_units=number_of_units,
            meal=meal,
            food_entry_name=food_entry_name,
        ), indent=2)

    @mcp.tool()
    def delete_entry(food_entry_id: str) -> str:
        """Delete a diary entry by food_entry_id (from get_diary)."""
        client.call("food_entry.delete", {"food_entry_id": str(food_entry_id)})
        return f"deleted entry {food_entry_id}"

    @mcp.tool()
    def create_custom_food(
        name: str,
        brand: str = "",
        calories: float = 0,
        protein: float = 0,
        fat: float = 0,
        carbs: float = 0,
        serving_size: str = "1 serving",
        serving_amount: float | None = None,
        serving_amount_unit: str = "g",
        brand_type: str = "manufacturer",
        calories_from_fat: float | None = None,
        saturated_fat: float | None = None,
        polyunsaturated_fat: float | None = None,
        monounsaturated_fat: float | None = None,
        trans_fat: float | None = None,
        cholesterol: float | None = None,
        sodium: float | None = None,
        potassium: float | None = None,
        fiber: float | None = None,
        sugar: float | None = None,
        added_sugars: float | None = None,
        vitamin_d: float | None = None,
        vitamin_a: float | None = None,
        vitamin_c: float | None = None,
        calcium: float | None = None,
        iron: float | None = None,
    ) -> str:
        """Create a custom food through FatSecret's Premier-only v2 method."""
        return json.dumps(_create_custom_food(
            client,
            name=name,
            brand=brand,
            calories=calories,
            protein=protein,
            fat=fat,
            carbs=carbs,
            serving_size=serving_size,
            serving_amount=serving_amount,
            serving_amount_unit=serving_amount_unit,
            brand_type=brand_type,
            calories_from_fat=calories_from_fat,
            saturated_fat=saturated_fat,
            polyunsaturated_fat=polyunsaturated_fat,
            monounsaturated_fat=monounsaturated_fat,
            trans_fat=trans_fat,
            cholesterol=cholesterol,
            sodium=sodium,
            potassium=potassium,
            fiber=fiber,
            sugar=sugar,
            added_sugars=added_sugars,
            vitamin_d=vitamin_d,
            vitamin_a=vitamin_a,
            vitamin_c=vitamin_c,
            calcium=calcium,
            iron=iron,
        ), indent=2)