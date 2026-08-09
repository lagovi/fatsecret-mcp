import json
import datetime
import time
from fatsecret_mcp.config import Config
from fatsecret_mcp.client import Client
from fatsecret_mcp.server import _register_tools

class DummyMCP:
    def __init__(self): self.tools = {}
    def tool(self, name=None, **kwargs):
        def decorator(fn):
            self.tools[name or fn.__name__] = fn
            return fn
        if callable(name):
            self.tools[name.__name__] = name
            return name
        return decorator

def extract_ids(obj):
    ids = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "food_entry_id":
                ids.add(int(v))
            else:
                ids.update(extract_ids(v))
    elif isinstance(obj, list):
        for item in obj:
            ids.update(extract_ids(item))
    return ids

def find_entry(obj, target_id):
    if isinstance(obj, dict):
        if str(obj.get("food_entry_id")) == str(target_id):
            return obj
        for v in obj.values():
            res = find_entry(v, target_id)
            if res: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_entry(item, target_id)
            if res: return res
    return None

def get_diary_ids(get_diary_func, date_iso):
    diary_str = get_diary_func(date_iso)
    if not diary_str.strip(): return set()
    try:
        return extract_ids(json.loads(diary_str))
    except:
        return set()

def main():
    today = datetime.date.today()
    test_date = (today - datetime.timedelta(days=2)).isoformat()
    test_date_2 = (today - datetime.timedelta(days=1)).isoformat()

    cfg = Config.load()
    client = Client(cfg.consumer, cfg.user_token)
    mcp = DummyMCP()
    _register_tools(mcp, client)
    tools = mcp.tools

    print("\n\n\n=== ЭТАЛОННЫЙ ИНТЕГРАЦИОННЫЙ ТЕСТ (15 ИНСТРУМЕНТОВ) ===\n")

    # --- ПРЕДОЧИСТКА ---
    print("--- 0. Очистка мусора с прошлых запусков ---")
    old_ids = get_diary_ids(tools["get_diary"], test_date)
    deleted_count = 0
    for oid in old_ids:
        try: 
            tools["delete_diary_entry"](oid)
            deleted_count += 1
            time.sleep(0.5)
        except: pass
    if deleted_count: print(f"Удалено {deleted_count} старых записей.")

    # 1. Profile
    prof = json.loads(tools["get_profile"]())
    assert "last_weight_date" in prof
    print("1. [УСПЕХ] get_profile")

    # 2. Search
    tools["search_food"](query="Milk", max_results=3)
    print("2. [УСПЕХ] search_food")

    # 3. Details
    tools["get_food_details"](794)
    print("3. [УСПЕХ] get_food_details")

    # 4. Favorites 
    tools["add_favorite"](794)
    favs = tools["get_favorites"]()
    assert "794" in favs, "Продукт не добавился в избранное"
    tools["delete_favorite"](794)
    favs_after = tools["get_favorites"]()
    assert "794" not in favs_after, "Продукт не удалился из избранного"
    print("4. [УСПЕХ] add/get/delete_favorite")

    # 5. Saved Meals (Тест обертки + надежное получение ID)
    sm_str = tools["get_saved_meals"]()
    assert isinstance(sm_str, str), "Обертка не вернула строку"
    print("5. [УСПЕХ] get_saved_meals")
    
    # Надежно берем сырой ID для следующих тестов
    sm_res = client.call("saved_meals.get.v2", {})
    sm_list = sm_res.get("saved_meals", {}).get("saved_meal", [])
    if isinstance(sm_list, dict): sm_list = [sm_list]
    
    if sm_list:
        sm_id = int(sm_list[0]["saved_meal_id"])
        
        # 6. Получение деталей Saved Meal
        sm_details = tools["get_saved_meal_details"](sm_id)
        assert str(sm_id) in sm_details or "food" in sm_details.lower()
        print("6. [УСПЕХ] get_saved_meal_details")

        # 7. Логирование Saved Meal
        ids_before = get_diary_ids(tools["get_diary"], test_date)
        tools["log_saved_meal"](saved_meal_id=sm_id, meal="snack", date=test_date)
        time.sleep(0.5)
        ids_after = get_diary_ids(tools["get_diary"], test_date)
        
        created_sm_ids = ids_after - ids_before
        assert created_sm_ids, "Записи Saved Meal не появились!"
        print(f"7. [УСПЕХ] log_saved_meal (Создано {len(created_sm_ids)} записей)")
        
        # Сразу чистим эту пачку
        for i in created_sm_ids: 
            tools["delete_diary_entry"](i)
            time.sleep(0.5)

    # 8. log_food_by_serving
    ids_before = get_diary_ids(tools["get_diary"], test_date)
    tools["log_food_by_serving"](food_id=794, serving_id=729, servings=1.5, meal="Lunch", date=test_date)
    time.sleep(0.5)
    ids_after = get_diary_ids(tools["get_diary"], test_date)
    
    entry1_id = (ids_after - ids_before).pop()
    print(f"8. [УСПЕХ] log_food_by_serving (Создан ID: {entry1_id})")

    # 9. log_food_by_amount
    ids_before = get_diary_ids(tools["get_diary"], test_date)
    tools["log_food_by_amount"](food_id=794, amount=250, unit="ml", meal="Snacks", date=test_date)
    time.sleep(0.5)
    ids_after = get_diary_ids(tools["get_diary"], test_date)
    
    entry2_id = (ids_after - ids_before).pop()
    print(f"9. [УСПЕХ] log_food_by_amount (Создан ID: {entry2_id})")

    # 10. update_diary_entry
    tools["update_diary_entry"](food_entry_id=entry1_id, serving_id=729, number_of_units=300, meal="Dinner")
    time.sleep(0.5)
    diary = json.loads(tools["get_diary"](test_date))
    updated_e = find_entry(diary, entry1_id)
    assert updated_e.get("meal") == "Dinner", "Прием пищи не обновился"
    print("10. [УСПЕХ] update_diary_entry (Запись успешно обновлена)")

    # 11. get_diary_range
    range_data = json.loads(tools["get_diary_range"](start_date=test_date, end_date=test_date_2))
    assert len(range_data["days"]) == 2
    print("11. [УСПЕХ] get_diary_range")

    # 12. Clean up (delete_diary_entry)
    tools["delete_diary_entry"](entry1_id)
    time.sleep(0.5)
    tools["delete_diary_entry"](entry2_id)
    print("12. [УСПЕХ] delete_diary_entry (Мусор убран)")

    print("\n=== ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОШЛИ БЕЗУПРЕЧНО! ===")

if __name__ == "__main__":
    main()
