"""Tag heuristics for order email import using TF-IDF + cosine similarity.

Uses scikit-learn's TfidfVectorizer to classify order item names into
semantic categories based on keyword-rich category descriptions.

TF-IDF provides better-than-keyword matching:
- Rare/unique words get higher weight (IDF)
- Common words across categories get suppressed
- Word n-grams (1-3) capture multi-word phrases
- No training data needed, just well-written descriptions
- ~200µs per classify() call, no GPU needed

Usage:
    classifier = SemanticTagClassifier()
    tags = classifier.classify(["Purina Pro Plan Dog Food 35lb", "Milk-Bone Treats"])
    # Returns: {"Pets": 0.45} if above confidence threshold

    resolver = TagResolver(paperless_client, config)
    tag_ids = resolver.resolve(["Pets", "Electronics & Tech"])
    # Auto-creates any missing Paperless tags, returns IDs
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Each description is a space-separated list of words/phrases that commonly
# appear in Amazon product names for that category.
# Written to maximize word-level n-gram overlap (1-3 words).
# IMPORTANT: include singular AND plural variants of key terms.
DEFAULT_CATEGORIES = {
    "Pets": (
        "dog food cat food dog treats puppy cat kitten "
        "dog leash collar harness dog crate cat bed litter box "
        "cat litter scratching post bird seed fish food aquarium "
        "tank filter pump gravel water conditioner reptile "
        "terrarium UVB heat lamp basking bulb hamster cage "
        "gerbil mouse rat bedding hay guinea pig rabbit "
        "flea tick treatment heartworm prevention grooming "
        "clippers nail grinder brush comb shampoo deodorizer "
        "pet bed carrier kennel crate chew toy bone dental "
        "poop bag dispenser training pad puppy pad litter scoop "
        "veterinary joint supplement glucosamine hip health"
    ),
    "Home Improvement": (
        "drill driver impact hammer saw circular miter jigsaw "
        "sander sander belt orbital random router table "
        "lumber plywood MDF board paint primer stain varnish "
        "brush roller tray painter tape drop cloth sandpaper "
        "level tape measure speed square stud finder knife "
        "electrical wire outlet switch receptacle dimmer "
        "plumbing PVC pipe copper fitting valve faucet toilet "
        "caulk silicone grout tile flooring laminate hardwood "
        "hinge door lock deadbolt knob cabinet pull drawer slide "
        "screw anchor nail bolt washer nut shelf bracket "
        "workbench tool box organizer pegboard storage rack "
        "light fixture ceiling fan chandelier sconce lamp "
        "adhesive glue epoxy super glue wood filler putty "
        "furniture assembly hardware cam lock dowel screw"
    ),
    "Electronics & Tech": (
        "laptop notebook tablet iPad iPad iPhone smartphone "
        "USB cable charger power bank adapter HDMI DisplayPort "
        "bluetooth headphones earbuds AirPods speaker soundbar "
        "monitor display screen keyboard mouse mousepad "
        "SSD solid state hard drive HDD external hard drive "
        "memory card SD microSD USB flash drive thumb drive "
        "batteries battery pack rechargeable AA AAA lithium "
        "router wifi extender mesh network access point switch "
        "security camera doorbell cam indoor outdoor "
        "smart home hub speaker Echo Google Nest thermostat "
        "screen protector tempered glass phone case cover "
        "wireless charger charging station stand dock "
        "printer scanner ink toner cartridge label maker "
        "webcam microphone headset gaming mouse keyboard "
        "smartwatch fitness band wearable apple watch band"
    ),
    "Kitchen & Dining": (
        "cookware frying pan skillet saucepan stockpot dutch oven "
        "chef knife paring knife knife set cutting board "
        "utensils spatula tongs ladle whisk peeler grater "
        "measuring cup measuring spoon mixing bowl colander "
        "bakeware baking sheet muffin pan loaf pan casserole "
        "blender immersion blender hand mixer stand mixer "
        "coffee maker Keurig espresso machine grinder press "
        "air fryer toaster toaster oven Instant Pot slow cooker "
        "dinner plate bowl mug glass tumbler wine flatware "
        "food storage container Tupperware glasslock mason jar "
        "spice rack salt pepper shaker oil dispenser utensil "
        "oven mitt pot holder trivet kitchen towel dish cloth "
        "dish rack drying mat sink mat faucet filter "
        "cutlery silverware steak knife butter knife serving "
        "kitchen shears pizza cutter vegetable peeler corer"
    ),
    "Automotive": (
        "motor oil synthetic conventional 5W-30 10W-30 0W-20 "
        "oil filter air filter cabin filter fuel filter "
        "windshield wiper blade beam hybrid winter "
        "car battery AGM lithium jump starter jumper cable "
        "tire inflator compressor gauge pressure monitor TPMS "
        "floor mat all weather carpet rubber trunk liner "
        "seat cover bucket bench leather neoprene universal "
        "dash camera dashcam front rear 4K night vision "
        "phone mount magnetic vent dashboard car holder "
        "car charger USB cigarette lighter fast charge "
        "wax polish sealant detailer microfiber cloth mitt "
        "interior cleaner protectant leather conditioner "
        "headlight bulb LED HID halogen fog light taillight "
        "touch up paint pen scratch remover clear coat "
        "car cover outdoor indoor sun shade windshield cover "
        "key fob cover keychain remote starter battery"
    ),
    "Health & Beauty": (
        "multivitamin vitamin D3 C B12 zinc magnesium supplement "
        "sunscreen SPF moisturizer face lotion serum cream "
        "face wash cleanser toner exfoliator mask sheet "
        "shampoo conditioner hair styling gel mousse spray "
        "deodorant antiperspirant spray stick roll on "
        "toothpaste whitening toothbrush electric manual floss "
        "razor blade shaving cartridge cream foam brush "
        "makeup foundation concealer powder blush eyeshadow "
        "lipstick lip gloss lip liner mascara eyeliner "
        "first aid kit bandage bandaid gauze tape antibiotic "
        "pain reliever ibuprofen acetaminophen naproxen aspirin "
        "allergy medicine antihistamine Claritin Zyrtec "
        "hand sanitizer face mask KN95 surgical disposable "
        "nail clipper file buffer cuticle remover foot care "
        "hair dryer straightener curler iron hot brush "
        "beard trimmer hair clipper electric shaver foil"
    ),
    "Office & Stationery": (
        "printer paper copy multipurpose letter legal "
        "laser toner cartridge HP Canon Brother inkjet ink "
        "pen ballpoint gel fountain rollerball fine point "
        "pencil mechanical lead 0.5 0.7 HB eraser sharpener "
        "notebook spiral bound composition journal moleskine "
        "binder ring divider tab folder file folder hanging "
        "scissors office shears utility knife box cutter "
        "tape dispenser scotch tape packing tape shipping "
        "stapler stapler staple remover staples paper clip "
        "whiteboard dry erase marker eraser spray cleaner "
        "calendar planner weekly monthly daily appointment "
        "envelope window invitation mailing padded bubble "
        "shipping box corrugated cardboard poly mailer label "
        "desk organizer drawer pencil cup letter tray "
        "mouse pad wrist rest monitor stand desk lamp "
        "paper shredder cross cut micro cut strip cut"
    ),
    "Garden & Outdoor": (
        "plant flower perennial annual shrub seedling bulb "
        "seed vegetable herb tomato pepper cucumber lettuce "
        "soil potting mix garden compost manure peat moss "
        "fertilizer plant food slow release liquid granular "
        "mulch bark wood chip rubber shredded cedar "
        "garden hose expandable soaker nozzle spray gun "
        "sprinkler oscillating impact rotary tripod base "
        "watering can rain barrel drip irrigation kit "
        "pruner shears clipper trimmer lopper hedge snips "
        "shovel spade trowel rake hoe cultivator fork tiller "
        "gloves gardening leather nitrile coated cotton "
        "patio chair sofa lounge set cushion umbrella table "
        "grill gas charcoal propane smoker BBQ cover "
        "grass seed lawn fertilizer weed killer moss control "
        "fire pit wood burning propane table outdoor heater "
        "bug zapper mosquito trap citronella candle lantern "
        "bird feeder house bath seed squirrel proof solar "
        "patio lights string lights solar pathway stake"
    ),
    "Books & Media": (
        "book hardcover paperback novel fiction nonfiction "
        "textbook reference manual guide cookbook biography "
        "great gatsby harry potter lord rings narnia hobbit "
        "dune to kill mockingbird pride prejudice catch "
        "audiobook CD MP3 audible narrated story "
        "Blu-ray 4K UHD movie film collection box set "
        "DVD video disc documentary series TV show "
        "music CD album artist band greatest hits "
        "vinyl record LP turntable accessory "
        "video game PlayStation Xbox Nintendo Switch PC "
        "board game strategy family party card game "
        "puzzle jigsaw piece completed puzzle mat "
        "magazine subscription issue monthly quarterly "
        "comic book graphic novel manga volume issue "
        "coffee table book photography drawing art book "
        "audiobook audible credit subscription listen"
    ),
    "Clothing & Accessories": (
        "shirt t-shirt button down polo Oxford long sleeve "
        "jeans denim straight slim bootcut relaxed stretch "
        "pants chino khaki cargo trouser jogger sweatpant "
        "shorts cargo athletic board swim trunk "
        "dress maxi mini sundress formal casual office "
        "jacket coat peacoat puffer bomber leather denim "
        "hoodie sweatshirt pullover zip crew neck "
        "sweater cardigan pullover cashmere wool cotton "
        "underwear boxer brief trunk hipster bikini bra "
        "socks crew ankle no show athletic wool compression "
        "sneakers running shoes walking athletic casual "
        "boots work hiking waterproof winter rain Chelsea "
        "sandals flip flop slide strappy flat summer "
        "backpack laptop bookbag daypack travel hiking "
        "wallet bifold trifold RFID money clip credit card "
        "belt leather canvas braided dress casual web "
        "hat baseball cap beanie bucket visor snapback "
        "sunglasses polarized aviator wayfarer running "
        "watch analog digital chronograph dress diver "
        "earrings necklace ring bracelet pendant charm"
    ),
    "Baby & Kids": (
        "baby formula powder liquid concentrate ready feed "
        "diaper size newborn 1 2 3 4 5 6 pull up training "
        "baby wipes sensitive unscented fragrance free "
        "diaper cream rash ointment barrier lotion baby oil "
        "baby bottle glass plastic silicone nipple flow "
        "pacifier soothie silicone orthodontic newborn "
        "sippy cup straw cup training cup spill proof "
        "bib burp cloth receiving blanket swaddle sleep sack "
        "baby monitor video audio WiFi night vision "
        "stroller travel system umbrella jogging lightweight "
        "car seat infant convertible booster harness base "
        "crib mattress sheet bumper rail guard portable "
        "pack n play bassinet co sleeper bedside "
        "toy building block LEGO DUPLO stacking sorting "
        "children book picture board interactive lift flap "
        "kids clothing pajama onesie romper footie sleeper "
        "toddler shirt pants shorts dress set outfit socks "
        "baby shoes crib shoe soft sole infant first walker"
    ),
    "Sports & Fitness": (
        "dumbbell barbell weight plate kettlebell adjustable "
        "resistance band loop tube pull up assist strap "
        "yoga mat exercise mat extra thick non slip "
        "exercise bike stationary spin recumbent upright "
        "treadmill running walking folding incline "
        "fitness tracker Fitbit Apple Garmin step heart "
        "gym bag duffel backpack equipment carry sports "
        "jump rope speed weighted cable crossfit "
        "foam roller massage stick lacrosse ball recovery "
        "protein powder whey isolate casein plant vegan "
        "shaker bottle blender ball protein mixer cup "
        "helmet bike cycling skateboard ski snowboard "
        "basketball outdoor indoor size rim net hoop "
        "soccer ball cleat shin guard goal net football "
        "camping tent person backpacking sleeping bag "
        "sleeping bag mummy rectangular synthetic down "
        "hiking backpack hydration pack daypack trekking "
        "fishing rod reel combo tackle box bait lure hook "
        "water bottle insulated stainless steel BPA free "
        "weight bench squat rack dumbbell rack gym equipment"
    ),
    "Arts & Crafts": (
        "paint acrylic oil watercolor gouache tempera "
        "paint brush flat round filbert angle liner "
        "canvas panel stretched canvas pad board easel "
        "drawing pencil graphite charcoal pastel sketch "
        "sketchbook drawing pad spiral bound paper "
        "colored pencil Crayola Prismacolor watercolor "
        "crayon washable twistable jumbo pip squeak "
        "marker permanent washable dry erase fine tip "
        "yarn acrylic wool cotton blend worsted bulky "
        "knitting needle circular double pointed set "
        "crochet hook ergonomic aluminum bamboo steel "
        "embroidery floss cross stitch thread DMC "
        "sewing machine mechanical computerized portable "
        "fabric cotton quilting fleece felt flannel "
        "scissors fabric shears embroidery snipper pinking "
        "glue gun hot glue stick tacky craft glue Elmers "
        "bead jewelry making charm pendant clasp wire "
        "jewelry finding earring hook jump ring lobster "
        "clay polymer air dry pottery ceramic sculpting "
        "scrapbook paper pad sticker album corner punch "
        "stencil template ruler cutting mat rotary cutter"
    ),
    "Groceries & Food": (
        "water bottle spring purified sparkling mineral "
        "soda cola root beer ginger ale lemon lime "
        "coffee bean ground roast whole arabica medium dark "
        "tea bag green black herbal chai oolong loose "
        "cereal breakfast oats granola muesli cheerios "
        "protein bar granola bar energy bar cliff kind "
        "trail mix nuts almonds cashews walnuts peanuts "
        "pasta spaghetti penne rotini linguine macaroni "
        "rice white brown basmati jasmine long grain wild "
        "canned vegetable corn green bean pea tomato sauce "
        "soup chicken noodle tomato cream mushroom "
        "pasta sauce marinara alfredo pesto arrabbiata "
        "olive oil extra virgin canola vegetable coconut "
        "vinegar balsamic white red wine apple cider "
        "salt kosher sea rock pink Himalayan table "
        "pepper black ground cracked tellicherry white "
        "ketchup mustard yellow Dijon whole grain honey "
        "mayonnaise miracle whip sandwich spread vegan "
        "peanut butter creamy crunchy natural organic "
        "jam jelly preserves marmalade fruit spread "
        "honey maple syrup agave stevia sugar substitute "
        "granola bar protein bar nut bar fruit snack"
    ),
}


class SemanticTagClassifier:
    """Classifies order item names into categories using TF-IDF + cosine similarity.

    Args:
        categories: Dict of {name: description}. Defaults to DEFAULT_CATEGORIES.
        min_confidence: Minimum cosine similarity (0.0-1.0) to assign a tag.
                        TF-IDF produces lower scores than embeddings.
                        Default 0.06 (tuned for word-level n-grams with stop words).
    """

    def __init__(
        self,
        categories: dict[str, str] = None,
        min_confidence: float = 0.06,
    ):
        self.categories = categories or dict(DEFAULT_CATEGORIES)
        self.min_confidence = min_confidence
        self._cat_names = list(self.categories.keys())
        self._cat_descriptions = list(self.categories.values())
        self._loaded = False
        self._vectorizer = None
        self._cat_vectors = None

    def _load(self):
        """Lazy-fit the TF-IDF vectorizer on category descriptions."""
        if self._loaded:
            return

        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        logger.info("Fitting TF-IDF vectorizer on category descriptions...")

        self._vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 3),
            min_df=1,
            max_df=0.85,
            sublinear_tf=True,
            stop_words="english",
        )

        # Fit on ALL descriptions + pre-compute category vectors
        self._cat_vectors = self._vectorizer.fit_transform(self._cat_descriptions)

        self._loaded = True
        logger.info(
            f"TF-IDF ready. {len(self.categories)} categories, "
            f"{len(self._vectorizer.get_feature_names_out())} n-gram features."
        )

    def classify(self, item_names: list[str]) -> dict[str, float]:
        """Classify item names into categories.

        Args:
            item_names: List of product/item name strings.

        Returns:
            Dict of {category_name: confidence_score} sorted by confidence
            descending. Only includes categories above min_confidence threshold.
        """
        if not item_names:
            return {}

        self._load()
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        # Transform item names to TF-IDF vectors
        item_vectors = self._vectorizer.transform(item_names)

        # Cosine similarity: items × categories
        similarities = cosine_similarity(item_vectors, self._cat_vectors)

        # Max similarity across all items for each category
        max_sim = similarities.max(axis=0)

        # Build result
        result = {}
        for i, name in enumerate(self._cat_names):
            score = float(max_sim[0, i]) if hasattr(max_sim, 'shape') and max_sim.ndim > 1 else float(max_sim[i])
            if score >= self.min_confidence:
                result[name] = score

        return dict(sorted(result.items(), key=lambda x: -x[1]))

    def classify_top(self, item_names: list[str]) -> Optional[str]:
        """Return the single best-matching category name, or None."""
        results = self.classify(item_names)
        return next(iter(results.keys())) if results else None


class TagResolver:
    """Resolves heuristic category names to Paperless tag IDs.

    Auto-creates tags in Paperless on first encounter.
    Caches the mapping in-memory for the lifetime of the resolver.

    Args:
        pl_client: PaperlessClient instance.
        categories: Dict of {name: {color, tag_id}} from config.
                    tag_id=0 means auto-create.
        auto_create: Whether to auto-create missing tags (default: True).
    """

    def __init__(
        self,
        pl_client,
        categories: dict = None,
        auto_create: bool = True,
    ):
        self._pl = pl_client
        self._auto_create = auto_create
        self._tag_id_map: dict[str, int] = {}  # category_name → paperless_tag_id
        self._resolved = False

        # Build lookup: category name → config overrides
        self._config = {}
        if categories:
            for name, opts in categories.items():
                if isinstance(opts, dict):
                    self._config[name] = opts
                elif isinstance(opts, int):
                    self._config[name] = {"tag_id": opts}

    def resolve(self, category_names: list[str]) -> list[int]:
        """Resolve category names to Paperless tag IDs.

        Tags are lazily discovered once, then cached.
        Missing tags are auto-created if auto_create=True.
        """
        if not category_names:
            return []

        if not self._resolved:
            self._load_existing_tags()

        tag_ids = []
        for name in category_names:
            if name in self._tag_id_map:
                tag_ids.append(self._tag_id_map[name])
            elif self._auto_create:
                tid = self._create_tag(name)
                if tid:
                    self._tag_id_map[name] = tid
                    tag_ids.append(tid)

        return tag_ids

    def _load_existing_tags(self):
        """Fetch all existing Paperless tags and build the name→ID map."""
        existing = self._pl.list_tags() or []
        for tag in existing:
            tag_name = tag.get("name", "")
            self._tag_id_map[tag_name] = tag["id"]

        self._resolved = True
        logger.debug(f"Loaded {len(existing)} existing tags from Paperless")

    def _create_tag(self, name: str) -> Optional[int]:
        """Create a new tag in Paperless."""
        cfg = self._config.get(name, {})
        color = cfg.get("color", "#a6cee3")

        # Check if a fixed ID was configured
        fixed_id = cfg.get("tag_id", 0)
        if isinstance(fixed_id, int) and fixed_id > 0:
            logger.info(f"Using configured tag ID {fixed_id} for '{name}'")
            return fixed_id

        # Create via Paperless API
        tag_data = {"name": name, "color": color, "is_inbox_tag": False}
        try:
            result = self._pl.create_tag(tag_data)
            if result and "id" in result:
                logger.info(f"Created Paperless tag '{name}' (id={result['id']})")
                return result["id"]
        except Exception as e:
            logger.warning(f"Failed to create tag '{name}': {e}")

        return None
