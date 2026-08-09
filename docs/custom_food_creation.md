# FatSecret Custom Food Creation: Architecture & Pitfalls Guide

## Overview
FatSecret limits custom food creation via the official REST API (`food.create.v2`) strictly to **Premier / Paid Tier** developer accounts.
To allow free-tier users to create custom foods, `fatsecret-mcp` uses headless browser web automation (`patchright`) targeting the FatSecret web interface.

---

## ❌ Non-Working Approaches & Known Pitfalls

### 1. FatSecret REST API v2 (`food.create.v2`)
- **Status:** **FAILED (Premier Only)**
- **Issue:** Returns permission error on free OAuth 1.0a credentials.

### 2. System Snap Chromium (`/snap/bin/chromium`)
- **Status:** **FAILED (Ubuntu 24.04 Snap issue)**
- **Issue:** Throws namespace mount errors (`cannot change mount namespace according to change mount...`).
- **Solution:** Always use Playwright/Patchright's native Chromium binaries from `~/.cache/ms-playwright`.

### 3. Querying Custom Food Details via API (`food.get.v4`)
- **Status:** **EXPECTED FAILURE (FatSecret API Error 106)**
- **Issue:** Calling `food.get.v4(food_id=custom_food_id)` returns `FatSecret error 106: Invalid ID`.
- **Reason:** `food.get.v4` only queries the global public database. Web-created custom foods belong to the user's personal web database.
- **Solution:** Catch `FatSecretError` (code 106) in `log_food_by_amount` and `log_food_by_serving`, fallback to `serving_id="0"` (where 1 unit = 100g).

### 4. Navigation & URL Redirection Pitfalls
- **Issue 1:** `https://foods.fatsecret.com/Default.aspx?pa=fcd` and `pa=f` redirect back to `Default.aspx?pa=m`.
- **Issue 2:** Generic link matching like `a:has-text("Food")` accidentally clicks the top global header tab (`https://foods.fatsecret.com/calories-nutrition/`).
- **Issue 3:** `page.wait_for_load_state("networkidle")` times out because FatSecret background tracking scripts keep connections open.
- **Solution:** Use `wait_until="domcontentloaded"` and navigate directly to the target URL: `https://foods.fatsecret.com/Diary.aspx?pa=fjcr`.

---

## ✅ Working Architecture (The Proven Way)

### 1. Direct Form Navigation
- **URL:** `https://foods.fatsecret.com/Diary.aspx?pa=fjcr`
- **Form Fields Breakdown:**
  - **Radio:** `name="manufacturerType"`, `value="0"` -> "My own custom food entry"
  - **Title:** `name="title"` -> Food / Item Name
  - **Serving Description:** `name="servingSize"` -> e.g. `100 g`
  - **Serving Amount:** `name="servingAmount"` -> `100`
  - **Serving Unit:** `name="servingAmountUnit"` -> `g`
  - **Calories:** `name="energyPerPortion"`
  - **Protein:** `name="proteinPerPortion"`
  - **Fat:** `name="fatPerPortion"`
  - **Carbs:** `name="carbohydratePerPortion"`
  - **Sharing:** `name="sharing"`, `value="2"` -> Private ("Don't share")
  - **Submit Button:** `page.locator('*:has-text("Save")').last`

### 2. Extracting `food_id`
- After clicking **Save**, FatSecret redirects to:
  `https://foods.fatsecret.com/Diary.aspx?pa=fjrd&rid={rid}&entryname=...&dt=...`
- The query parameter **`rid`** is the exact `food_id` of the newly created item!

### 3. Instant Searchability
- The newly created product immediately becomes searchable via `foods.search(query=name)` and returns its `food_id` (`rid`).

### 4. Logging Custom Foods into Diary
- Custom foods use `serving_id = "0"`.
- To log $ grams of a custom food:
  ```python
  api_units = amount_g / 100.0  # Custom foods are based on 100g base
  client.call("food_entry.create", {
      "food_id": str(food_id),
      "food_entry_name": name,
      "serving_id": "0",
      "number_of_units": f"{api_units:.4f}",
      "meal": meal,
      "date": date_int
  })
  ```
