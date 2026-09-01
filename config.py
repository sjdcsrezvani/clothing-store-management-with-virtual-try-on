import os
import secrets
from dotenv import load_dotenv

load_dotenv()

SMS_GATEWAY_URL = os.getenv("SMS_GATEWAY_URL", "http://185.214.101.206")
# No usable API-key fallback belongs in source. Configure this in .env or
# the Settings table; an empty value safely disables SMS until configured.
SMS_API_KEY = os.getenv("SMS_API_KEY", "")
SMS_DEVICE_ID = os.getenv("SMS_DEVICE_ID", "2")  # saj1093
# First-run admin password (seeded into the settings table as a hash on
# startup). If unset, admin login is disabled — never fall back to a
# publicly-known default.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./referral.db")
DEFAULT_REFERRER_DISCOUNT = int(os.getenv("DEFAULT_REFERRER_DISCOUNT", "50000"))
DEFAULT_REFERRED_DISCOUNT = int(os.getenv("DEFAULT_REFERRED_DISCOUNT", "30000"))
TRYON_API_URL = os.getenv("TRYON_API_URL") or "https://api.gapgpt.app/v1/images/edits"
TRYON_API_KEY = os.getenv("TRYON_API_KEY", "")
PUTER_TOKEN = os.getenv("PUTER_TOKEN", "")

# Session-signing secret. Random per start when unset (logs everyone out on
# restart); set it in .env to keep sessions across restarts.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)

# Token that unlocks the /api/* endpoints for the phone app (photo upload,
# try-on). If empty, /api/* requires an admin login session instead.
API_TOKEN = os.getenv("API_TOKEN", "")

# ── Try-on background presets ──
# Only realistic photo-studio backdrops. Each prompt is editable independently.
# `props` are on-topic in-studio styling items; the router picks 1–2 at random
# per generation so the scene feels different every time.
# Each item's `key` is the value sent through the form; `prompt` is the
# base backdrop instruction; `props` is the random-styling pool.
TRYON_BACKGROUNDS = [
    {
        "key": "kids",
        "label_fa": "🧒 استودیو کودک",
        "options": [
            {"key": "kids_classic", "label_fa": "📸 استودیو کلاسیک با پرده سفید",
             "prompt": "a professional photo studio with a clean white seamless paper backdrop, soft overhead key light and two diffused fill lights, gentle wraparound shadows, neutral grey floor",
             "props": [
                 "a small wooden stool beside the person",
                 "a woven wicker basket on the floor",
                 "a soft knit blanket draped on a low bench",
                 "a vintage wooden toy on the floor",
                 "a few pastel balloons tied to a small weight",
                 "a stack of hardcover children's books on a side table",
                 "a pair of tiny leather shoes placed on the floor",
                 "a small potted plant in a ceramic pot in the corner",
             ]},
            {"key": "kids_warm", "label_fa": "🟫 استودیو گرم با پس‌زمینه بژ",
             "prompt": "a professional photo studio with a warm beige seamless paper backdrop, soft golden key light from camera-left, two diffused fill lights, smooth gradient on the backdrop, neutral floor",
             "props": [
                 "a rustic wooden crate on the floor",
                 "a small woven rug under the person",
                 "a teddy bear sitting on a wooden bench",
                 "a bouquet of dried pampas grass in a ceramic vase",
                 "a straw hat resting on a low stool",
                 "a couple of stacked linen cushions",
                 "a vintage brass lantern on the floor",
                 "a soft knitted throw draped over a stool",
             ]},
            {"key": "kids_pastel", "label_fa": "🩵 استودیو پاستلی",
             "prompt": "a professional photo studio with a soft pastel blue seamless paper backdrop, large softbox key light above, two diffused side fills, subtle gradient on the backdrop, clean neutral floor",
             "props": [
                 "a small pastel balloon arch behind the person",
                 "a pastel rainbow stacking toy on the floor",
                 "a soft cotton cloud cushion on a low bench",
                 "a few pastel-colored wooden blocks arranged neatly",
                 "a pastel tulle skirt draped over a small stool",
                 "a small plush unicorn toy on the floor",
                 "a pastel paper lantern hanging from a discreet studio stand",
                 "a light blue knit blanket folded on a bench",
             ]},
            {"key": "kids_minimal", "label_fa": "⬜ استودیو مینیمال خاکستری",
             "prompt": "a professional photo studio with a neutral mid-grey seamless paper backdrop, large overhead softbox key light, two diffused side fills, fine tonal gradient on the backdrop, polished concrete floor",
             "props": [
                 "a single monochrome wooden block on the floor",
                 "a clean white ceramic vase with one dried branch",
                 "a plain linen stool beside the person",
                 "a small concrete planter with a green succulent",
                 "a single framed line-art print leaning against the backdrop",
                 "a minimalist beige ottoman",
                 "a soft grey throw on a low bench",
                 "a small wooden toy car on the floor",
             ]},
            {"key": "kids_window", "label_fa": "🪟 استودیو با نور پنجره",
             "prompt": "a professional photo studio setup recreating soft natural window light, sheer white diffuser on one side as key, subtle bounce fill, clean off-white seamless backdrop, light hardwood floor",
             "props": [
                 "a sheer white curtain softly billowing on the side",
                 "a small rattan side table",
                 "a potted olive plant in a terracotta pot",
                 "a linen cushion on a wooden bench",
                 "a stack of neutral-toned picture books",
                 "a soft cream knit throw on a stool",
                 "a small wicker basket on the floor",
                 "a tiny wooden rocking horse behind the person",
             ]},
            {"key": "kids_spring_garden", "label_fa": "🌸 استودیو باغ بهاری",
             "prompt": "a bright indoor photo studio with a soft floral seamless backdrop of pale pink and sage green, large overhead softbox key light, two diffused side fills, light wood floor",
             "props": [
                 "a small woven basket of dried flowers on the floor",
                 "a pastel watering can on a low stool",
                 "a branch of cherry blossoms leaning against the backdrop",
                 "a small ceramic bird figurine on a side table",
                 "a folded gingham blanket on a bench",
                 "a pair of tiny rain boots placed on the floor",
                 "a stack of seed packets on a wooden crate",
                 "a small potted lavender plant in a terracotta pot",
             ]},
            {"key": "kids_bookshelf", "label_fa": "📚 استودیو قفسه کتاب",
             "prompt": "a warm photo studio with a soft cream seamless paper backdrop flanked by tall warm-wood bookshelves slightly out of focus, large overhead softbox key light, two diffused side fills, polished wood floor",
             "props": [
                 "a stack of hardcover picture books on a low bench",
                 "a small leather armchair beside the person",
                 "a vintage brass reading lamp on a side table",
                 "a folded knitted throw on the armchair",
                 "a small globe on a wooden stand",
                 "a pair of small binoculars on the floor",
                 "a potted fern on a stool",
                 "a stack of story books with a soft toy on top",
             ]},
            {"key": "kids_playroom", "label_fa": "🧸 استودیو اتاق بازی",
             "prompt": "a cozy photo studio with a soft warm-yellow seamless backdrop, overhead softbox key light, two diffused side fills, light wood floor with a small rug",
             "props": [
                 "a small wooden abacus on a low bench",
                 "a stack of wooden building blocks on the floor",
                 "a soft plush bunny sitting on a stool",
                 "a small chalkboard easel behind the person",
                 "a folded pastel blanket on a bench",
                 "a pair of tiny felt slippers on the floor",
                 "a small wooden train on a side table",
                 "a basket of pinecones in the corner",
             ]},
            {"key": "kids_beach_white", "label_fa": "🏖️ استودیو ساحلی روشن",
             "prompt": "a sunlit photo studio with a soft sand-cream seamless backdrop, large overhead softbox key light, two diffused fills, light bleached-oak floor",
             "props": [
                 "a small woven straw tote on the floor",
                 "a pair of canvas sneakers placed neatly",
                 "a soft linen bucket hat on a stool",
                 "a folded cotton throw on a low bench",
                 "a small potted palm in a rattan basket",
                 "a seashell on a wooden side table",
                 "a small wooden boat on the floor",
                 "a pair of sunglasses resting on a folded towel",
             ]},
            {"key": "kids_terracotta", "label_fa": "🧱 استودیو تراکوتا",
             "prompt": "a warm photo studio with a soft terracotta seamless paper backdrop, large softbox key light from camera-left, two diffused fills, polished concrete floor",
             "props": [
                 "a small terracotta pot with a green herb on a stool",
                 "a folded cream linen throw on a bench",
                 "a pair of tan leather sandals on the floor",
                 "a woven basket of dried oranges on a side table",
                 "a small ceramic jug beside the person",
                 "a wooden picture frame leaning against the backdrop",
                 "a sprig of dried wheat in a slim vase",
                 "a folded raw-cotton blanket on a low stool",
             ]},
            {"key": "kids_navy", "label_fa": "🌊 استودیو سرمه‌ای",
             "prompt": "a moody photo studio with a deep navy seamless paper backdrop, overhead softbox key light, two diffused fills, smooth studio floor",
             "props": [
                 "a small model sailboat on a low bench",
                 "a folded striped cotton throw on a stool",
                 "a pair of white canvas sneakers on the floor",
                 "a brass telescope on a wooden side table",
                 "a small anchor figurine beside the person",
                 "a stack of nautical-themed picture books",
                 "a small woven navy rug under the person",
                 "a potted succulent in a blue ceramic pot",
             ]},
            {"key": "kids_pink_floral", "label_fa": "🌷 استودیو صورتی گلدار",
             "prompt": "a bright photo studio with a soft blush-pink seamless paper backdrop printed with subtle white floral silhouettes, large overhead softbox key light, two diffused fills, light wood floor",
             "props": [
                 "a small bouquet of dried pink roses on a stool",
                 "a folded floral cotton throw on a bench",
                 "a vintage floral teacup on a side table",
                 "a small woven heart basket on the floor",
                 "a pair of soft ballet slippers on a low bench",
                 "a small potted rose plant in a ceramic pot",
                 "a stack of pastel greeting cards on a side table",
                 "a pearl necklace laid on a velvet cushion",
             ]},
        ],
    },
    {
        "key": "teens",
        "label_fa": "🧑 استودیو نوجوان",
        "options": [
            {"key": "teens_dark", "label_fa": "⬛ استودیو تیره با ریم‌لایت",
             "prompt": "a professional photo studio with a charcoal-grey seamless paper backdrop, strong key softbox at 45 degrees from camera-right, subtle rim light from behind, polished floor with soft reflection",
             "props": [
                 "a single black director's chair in the background",
                 "a vintage film camera on a small black side table",
                 "a single studio light stand visible at the edge",
                 "a black leather ottoman on the floor",
                 "a pair of white sneakers placed on the floor",
                 "a black fedora resting on a stool",
                 "a small abstract sculpture on a pedestal",
                 "a folded denim jacket on a bench",
             ]},
            {"key": "teens_smoke", "label_fa": "🌫️ استودیو مه‌آلود",
             "prompt": "a professional photo studio with a deep smoke-grey seamless backdrop, single overhead key softbox, two diffused side fills, gentle atmospheric haze catching the light, neutral floor",
             "props": [
                 "a matte black skateboard leaning against the backdrop",
                 "a pair of over-ear headphones on a small stool",
                 "a folded hoodie on a low bench",
                 "a single vinyl record leaning against the wall",
                 "a baseball cap on a stool",
                 "a small concrete planter with a green plant",
                 "a leather messenger bag on the floor",
                 "a camera on a tripod at the edge of frame",
             ]},
            {"key": "teens_warm", "label_fa": "🟧 استودیو گرم",
             "prompt": "a professional photo studio with a warm umber seamless paper backdrop, large soft key light, two diffused fill lights, soft gradient on the backdrop, polished wood floor",
             "props": [
                 "a leather armchair behind the person",
                 "a vintage brass floor lamp",
                 "a small wooden side table with a coffee cup",
                 "a stack of vinyl records on the floor",
                 "a knitted throw draped on a wooden bench",
                 "a potted monstera plant in a terracotta pot",
                 "a pair of tan leather boots on the floor",
                 "a folded suede jacket on a stool",
             ]},
            {"key": "teens_white", "label_fa": "⬜ استودیو های‌کی",
             "prompt": "a high-key professional photo studio with a bright white seamless backdrop and bright white floor, large overhead softbox key, strong front fill, soft wraparound shadows, no visible studio gear",
             "props": [
                 "a single white cube pedestal",
                 "a pair of clean white sneakers on the floor",
                 "a small white ceramic vase with a green sprig",
                 "a folded white linen throw on a low stool",
                 "a minimalist white framed photo leaning against the backdrop",
                 "a small white beanbag on the floor",
                 "a single white balloon floating in the background",
                 "a transparent acrylic stool",
             ]},
            {"key": "teens_gradient", "label_fa": "🎨 استودیو گرادینت",
             "prompt": "a professional photo studio with a smooth light-to-dark grey seamless backdrop, main softbox key from camera-left, rim light from behind, clean studio floor with soft contact shadow",
             "props": [
                 "a single glass bottle on the floor",
                 "a leather watch laid on a small grey pedestal",
                 "a folded crew-neck sweatshirt on a stool",
                 "a small chrome desk lamp in the background",
                 "a pair of black-and-white canvas sneakers",
                 "a single dried cotton branch in a slim vase",
                 "a minimalist grey cube on the floor",
                 "a vinyl record on a low bench",
             ]},
            {"key": "teens_loft_brick", "label_fa": "🧱 استودیو آجری Loft",
             "prompt": "an urban loft photo studio with an exposed-brick backdrop softly lit, large overhead softbox key light, two diffused fills, polished concrete floor",
             "props": [
                 "a vintage leather duffel on the floor",
                 "a small matte-black floor lamp in the corner",
                 "a folded denim jacket on a wooden bench",
                 "a pair of worn canvas sneakers on the floor",
                 "a stack of vinyl records on a side table",
                 "a potted monstera in a black ceramic pot",
                 "a single Edison bulb hanging from a stand",
                 "a vintage paperback stack on the floor",
             ]},
            {"key": "teens_cafe", "label_fa": "☕ استودیو کافه",
             "prompt": "a cozy cafe-style photo studio with a warm cocoa-brown seamless backdrop, large overhead softbox key light, two diffused fills, light wood floor",
             "props": [
                 "a ceramic coffee cup on a small round table",
                 "a folded newspaper on a wooden bench",
                 "a small potted succulent on a side table",
                 "a pair of leather boots on the floor",
                 "a single hardcover book on the table",
                 "a vintage brass napkin holder beside the person",
                 "a folded wool scarf on a stool",
                 "a small bouquet of dried wheat in a ceramic jug",
             ]},
            {"key": "teens_street_soft", "label_fa": "🚶 استودیو خیابان نرم",
             "prompt": "an outdoor street-photo studio with a softly blurred neutral-concrete backdrop, large overhead softbox key light, two diffused fills, clean light pavement",
             "props": [
                 "a folded single-speed bicycle leaning against the backdrop",
                 "a vintage leather backpack on a low bench",
                 "a pair of clean white sneakers on the floor",
                 "a baseball cap on a stool",
                 "a small stack of magazines on a wooden crate",
                 "a potted plant on a small side table",
                 "a single film camera on a tripod behind the person",
                 "a folded flannel shirt on a bench",
             ]},
            {"key": "teens_gym_mirror", "label_fa": "🏋️ استودیو سالن ورزشی",
             "prompt": "a modern athletic photo studio with a deep slate-grey seamless backdrop, strong overhead softbox key light, two diffused side fills, polished rubber-style floor",
             "props": [
                 "a single foam roller on a low bench",
                 "a folded towel on a wooden stool",
                 "a pair of athletic sneakers on the floor",
                 "a stainless steel water bottle on a side table",
                 "a pair of boxing gloves on a low bench",
                 "a yoga mat rolled against the backdrop",
                 "a small weight plate on the floor",
                 "a sports duffel on a stool",
             ]},
            {"key": "teens_sunset_balcony", "label_fa": "🌇 استودیو بالکن غروب",
             "prompt": "a golden-hour photo studio with a warm peach-to-amber seamless backdrop simulating sunset, large softbox key from camera-left, two diffused fills, light wood floor",
             "props": [
                 "a small potted olive tree on a stool",
                 "a pair of tan suede boots on the floor",
                 "a folded linen shirt on a wooden bench",
                 "a ceramic mug on a small round table",
                 "a stack of paperbacks on a side table",
                 "a pair of sunglasses on a folded hat",
                 "a small brass lantern beside the person",
                 "a folded cotton throw on a stool",
             ]},
            {"key": "teens_snow_window", "label_fa": "❄️ استودیو پنجره برفی",
             "prompt": "a winter photo studio with a soft cool-white seamless backdrop, sheer white diffuser as key, two diffused fills, light wood floor, hints of cool blue rim light",
             "props": [
                 "a folded chunky knit sweater on a stool",
                 "a pair of fur-lined boots on the floor",
                 "a ceramic mug of cocoa on a small side table",
                 "a folded plaid blanket on a wooden bench",
                 "a pine branch in a ceramic vase",
                 "a single candle on a side table",
                 "a pair of leather gloves on a bench",
                 "a stack of vintage novels on the floor",
             ]},
            {"key": "teens_concrete_loft", "label_fa": "🏙️ استودیو بتنی",
             "prompt": "a minimalist photo studio with a raw concrete-textured seamless backdrop, overhead softbox key light, two diffused side fills, polished concrete floor",
             "props": [
                 "a single matte-black geometric sculpture on a pedestal",
                 "a pair of monochrome canvas sneakers on the floor",
                 "a folded charcoal sweatshirt on a stool",
                 "a small chrome desk lamp in the corner",
                 "a leather wallet on a low bench",
                 "a single green plant in a black concrete planter",
                 "a vintage film camera on a small side table",
                 "a folded grey wool overcoat on a bench",
             ]},
        ],
    },
]

# ── Try-on pose modes ──
# Controls how the child's pose is handled in the generated image.
# `key` is the value sent through the form; `prompt_clause` is appended to the
# base try-on prompt to steer pose preservation or AI-chosen pose.
TRYON_POSE_MODES = [
    {"key": "preserve", "label_fa": "🪄 حفظ ژست عکس اصلی",
     "prompt_clause": (
         "Preserve the person's exact face, body shape and posture — sitting stays sitting, standing stays standing, "
         "kneeling stays kneeling. Do not change pose, body shape or weight distribution. Only clothing changes."
     )},
    {"key": "ai", "label_fa": "✨ ژست انتخابی هوش مصنوعی",
     "prompt_clause": (
         "Catalog stance — standing, relaxed shoulders, gentle smile, one hand slightly out, three-quarter to camera, "
         "natural weight. Confident, comfortable. Keep face, body shape, skin tone, hair and expression identical to reference."
     )},
    {"key": "dynamic", "label_fa": "🎬 ژست پویا و صمیمی",
     "prompt_clause": (
         "Candid mid-motion moment — relaxed laugh, mid-stride with hem caught in motion, or both hands on fabric. "
         "Soft fabric movement, hair in air. Premium editorial framing; authentic, not rigid. "
         "Face, body shape, skin tone, expression unchanged."
     )},
]

# ── Try-on face modes ──
# Controls how the child's/teen's face is handled in the generated image.
# `key` is the value sent through the form; `prompt_clause` is the FACE RULE
# appended after the pose clause (it lands after MAIN_PROMPT so it overrides
# its generic identity wording). All modes keep the face structure intact;
# they only differ in skin beautification and expression.
TRYON_FACE_MODES = [
    {"key": "preserve", "label_fa": "🪞 بدون تغییر چهره — دقیقاً مثل عکس (پیشنهادی)",
     "prompt_clause": (
         "FACE RULE: preserve the person's face EXACTLY as in the reference photo — same facial "
         "structure, same expression, same skin texture, same look. NO beautification, no retouching, "
         "no skin-smoothing, no makeup, no expression change, no aging."
     )},
    {"key": "beautify", "label_fa": "✨ زیبا‌سازی ملایم — بدون آرایش (همان حالت و لبخند)",
     "prompt_clause": (
         "FACE RULE: keep the face position, pose, and expression EXACTLY as in the person's photo. "
         "Keep the face structure identical — face shape, nose, eyes, brows, lips, jawline, chin, "
         "ears, hairline, skin tone, and apparent age. Beautify only the skin: clean away acne, "
         "spots, and blemishes, and smooth it subtly for a healthy natural glow. "
         "This must look like natural healthy skin — NOT makeup, NOT a filter, NOT an airbrushed doll, "
         "NOT a changed face."
     )},
    {"key": "beautify_expression", "label_fa": "😊 زیبا‌سازی + لبخند طبیعی بهتر",
     "prompt_clause": (
         "FACE RULE: keep the face structure identical — face shape, nose, eyes, brows, lips, "
         "jawline, chin, ears, hairline, skin tone, and apparent age. Beautify the skin: clean away "
         "acne, spots, and blemishes and give it a subtle healthy glow — natural skin, NOT makeup, "
         "NOT a filter, NOT airbrushed plastic skin. And give the person a warmer, brighter "
         "expression: a small natural smile with soft, relaxed, happy eyes. "
         "Never change the face shape, features, or apparent age."
     )},
]