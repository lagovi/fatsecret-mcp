# FatSecret API Quirk: Favorites and Decimal Portions

## The Problem
According to the FatSecret API documentation for `food.add_favorite` and `food.delete_favorite`, the parameters `serving_id` and `number_of_units` are optional.

However, extensive integration testing (August 2026) revealed a critical bug on the FatSecret backend:
1. If a food is added to favorites with a decimal `number_of_units` (e.g., `2.5`), the API successfully saves it (saving it internally as `2.500`).
2. When attempting to DELETE this favorite using the exact same parameters (`number_of_units=2.5` or `number_of_units=2.500`), the API responds with:
   `FatSecret error 106: Invalid ID: please check your food_id / serving_id / number_of_units`.
3. Attempting to delete this stuck record by ignoring the portion parameters and sending only `food_id` ALSO returns Error 106.

**Result:** The food item becomes permanently "stuck" in the user's favorites and can only be removed manually via the FatSecret mobile app or website.

## Architectural Decision
To protect the user's account from becoming cluttered with undeletable favorite items, we made the architectural decision to **strictly remove** `serving_id` and `number_of_units` from the MCP tool implementation. 

Our MCP tools `add_favorite` and `delete_favorite` now ONLY accept `food_id`. 
The Favorites list acts simply as a "bookmark" for products. The Agent will specify the exact serving and amount later during the actual logging phase (`log_food_by_serving` or `log_food_by_amount`). This approach is 100% stable.
