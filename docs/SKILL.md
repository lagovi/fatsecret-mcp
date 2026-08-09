---
name: fatsecret-mcp
description: "FatSecret API MCP: food search, diary, tracking."
version: 2.0.0
author: Igor (updated)
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [nutrition, fatsecret, food, diary]
---

# FatSecret MCP Server Integration

This skill provides access to the FatSecret food database, diary logging, and nutrition tracking via the Model Context Protocol.

## ⚠️ GOLDEN RULES (CRITICAL)

1. **Language Limitation:** API only understands English. You MUST translate all non-English (e.g., Russian/Cyrillic) queries to English before using `search_food`.
2. **No Hallucinated IDs:** NEVER invent `food_id`, `serving_id`, `food_entry_id`, or `saved_meal_id`. Always look them up first.
3. **Past Logging Works:** You CAN log food in the past. Use the `date` parameter (`YYYY-MM-DD`) in logging tools.
4. **Favorites are Bookmarks:** `add_favorite` and `delete_favorite` only require `food_id`. They do not take portion parameters.
5. **Custom Foods Disabled:** `create_custom_food` is a stub and currently unavailable.
6. If a user says to add cookies, jam, pork, etc. without specifying details, use the basic products in English - cookies, jam, pork, etc.

## Tool Routing & Workflows

### 1. Logging Food (Two paths)

**Path A: By absolute weight/volume (Recommended for grams/ml)**
`search_food` -> `log_food_by_amount`
*Example:* User ate 150g of chicken. Search "chicken", get food_id, call `log_food_by_amount(amount=150, unit="g")`. The server calculates servings automatically.

**Path B: By specific serving (Pieces, cups, bars)**
`search_food` -> `get_food_details` -> `log_food_by_serving`
*Example:* User ate 1 medium apple. Search "apple", call `get_food_details` to find the exact `serving_id` for "1 medium", then call `log_food_by_serving`.

*Note:* You can leave `food_entry_name` empty when logging, the server will auto-fill it from the database.

### 2. Managing the Diary

To modify or delete entries, you must know the exact `food_entry_id`.
`get_diary` (for one day) OR `get_diary_range` (for multiple days) -> `update_diary_entry` OR `delete_diary_entry`.

### 3. Favorites & Saved Meals

- **Favorites:** Use `search_food` -> `add_favorite(food_id)`.
- **Saved Meals (Favorite Meals):** Use `get_saved_meals` -> `get_saved_meal_details` (optional) -> `log_saved_meal`.

## Parameter Tips
- `meal` parameter normalizes automatically. "Snack", "Snacks" or "Other" will all be logged as "Other".
- IDs can be passed as strings or integers, the server handles both. 
- Always verify data through `get_diary` after making changes if the user asks for confirmation.