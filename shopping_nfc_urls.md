# Shopping NFC Tag URLs

Generic add form tag:

```text
http://192.168.1.205:8090/form?token=ByS5xBg4TYAAVxBFM8bew
```

Item-specific NFC tag URLs:

- Corn Flakes: `http://192.168.1.205:8090/t/corn-flakes`
- Muesli: `http://192.168.1.205:8090/t/muesli`
- Yogurt: `http://192.168.1.205:8090/t/yogurt`
- Method Bathroom Cleaner: `http://192.168.1.205:8090/t/method-bathroom-cleaner`
- Method kitchen cleaner: `http://192.168.1.205:8090/t/method-kitchen-cleaner`
- Peanut butter: `http://192.168.1.205:8090/t/peanut-butter`
- Bananas: `http://192.168.1.205:8090/t/bananas`
- Granulated Sugar: `http://192.168.1.205:8090/t/granulated-sugar`
- Castor Sugar: `http://192.168.1.205:8090/t/castor-sugar`
- Eggs: `http://192.168.1.205:8090/t/eggs`
- Bread Flour: `http://192.168.1.205:8090/t/bread-flour`
- Cake Flour: `http://192.168.1.205:8090/t/cake-flour`
- Wholewheat Flour: `http://192.168.1.205:8090/t/wholewheat-flour`
- Baking Powder: `http://192.168.1.205:8090/t/baking-powder`
- Frozen Peas: `http://192.168.1.205:8090/t/frozen-peas`
- Frozen Chips: `http://192.168.1.205:8090/t/frozen-chips`
- Frozen Corn: `http://192.168.1.205:8090/t/frozen-corn`
- Tinned Tomatoes: `http://192.168.1.205:8090/t/tinned-tomatoes`
- Baked Beans: `http://192.168.1.205:8090/t/baked-beans`
- Oats: `http://192.168.1.205:8090/t/oats`
- Tucs Crackers: `http://192.168.1.205:8090/t/tucs-crackers`
- Raisins: `http://192.168.1.205:8090/t/raisins`
- Cheese Twists: `http://192.168.1.205:8090/t/cheese-twists`
- Spreadable butter: `http://192.168.1.205:8090/t/spreadable-butter`
- Unsalted butter: `http://192.168.1.205:8090/t/unsalted-butter`
- Fish fingers: `http://192.168.1.205:8090/t/fish-fingers`
- Honey: `http://192.168.1.205:8090/t/honey`
- Cashews: `http://192.168.1.205:8090/t/cashews`
- YoYos: `http://192.168.1.205:8090/t/yoyos`
- Dried Cranberries: `http://192.168.1.205:8090/t/dried-cranberries`
- Ketchup: `http://192.168.1.205:8090/t/ketchup`
- General Fruit: `http://192.168.1.205:8090/t/general-fruit`
- General Veg: `http://192.168.1.205:8090/t/general-veg`

## Config tags snippet

Paste or merge this under the `"tags"` key in `config/shopping_service.json`:

```json
{
  "corn-flakes": {
    "item": "Corn Flakes",
    "category": "Cereal"
  },
  "muesli": {
    "item": "Muesli",
    "category": "Cereal"
  },
  "yogurt": {
    "item": "Yogurt",
    "category": "Fridge"
  },
  "method-bathroom-cleaner": {
    "item": "Method Bathroom Cleaner",
    "category": "Household"
  },
  "method-kitchen-cleaner": {
    "item": "Method kitchen cleaner",
    "category": "Household"
  },
  "peanut-butter": {
    "item": "Peanut butter",
    "category": "Pantry"
  },
  "bananas": {
    "item": "Bananas",
    "category": "Fruit"
  },
  "granulated-sugar": {
    "item": "Granulated Sugar",
    "category": "Baking"
  },
  "castor-sugar": {
    "item": "Castor Sugar",
    "category": "Baking"
  },
  "eggs": {
    "item": "Eggs",
    "category": "Fridge"
  },
  "bread-flour": {
    "item": "Bread Flour",
    "category": "Baking"
  },
  "cake-flour": {
    "item": "Cake Flour",
    "category": "Baking"
  },
  "wholewheat-flour": {
    "item": "Wholewheat Flour",
    "category": "Baking"
  },
  "baking-powder": {
    "item": "Baking Powder",
    "category": "Baking"
  },
  "frozen-peas": {
    "item": "Frozen Peas",
    "category": "Freezer"
  },
  "frozen-chips": {
    "item": "Frozen Chips",
    "category": "Freezer"
  },
  "frozen-corn": {
    "item": "Frozen Corn",
    "category": "Freezer"
  },
  "tinned-tomatoes": {
    "item": "Tinned Tomatoes",
    "category": "Tins"
  },
  "baked-beans": {
    "item": "Baked Beans",
    "category": "Tins"
  },
  "oats": {
    "item": "Oats",
    "category": "Pantry"
  },
  "tucs-crackers": {
    "item": "Tucs Crackers",
    "category": "Snacks"
  },
  "raisins": {
    "item": "Raisins",
    "category": "Snacks"
  },
  "cheese-twists": {
    "item": "Cheese Twists",
    "category": "Snacks"
  },
  "spreadable-butter": {
    "item": "Spreadable butter",
    "category": "Fridge"
  },
  "unsalted-butter": {
    "item": "Unsalted butter",
    "category": "Fridge"
  },
  "fish-fingers": {
    "item": "Fish fingers",
    "category": "Freezer"
  },
  "honey": {
    "item": "Honey",
    "category": "Pantry"
  },
  "cashews": {
    "item": "Cashews",
    "category": "Snacks"
  },
  "yoyos": {
    "item": "YoYos",
    "category": "Snacks"
  },
  "dried-cranberries": {
    "item": "Dried Cranberries",
    "category": "Snacks"
  },
  "ketchup": {
    "item": "Ketchup",
    "category": "Condiments"
  },
  "general-fruit": {
    "item": "General Fruit",
    "category": "Fruit"
  },
  "general-veg": {
    "item": "General Veg",
    "category": "Vegetables"
  }
}
```