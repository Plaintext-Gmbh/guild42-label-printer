from flask import Flask, request, render_template, jsonify, redirect
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send
from brother_ql.raster import BrotherQLRaster
from PIL import Image, ImageDraw, ImageFont
import hmac
import os

app = Flask(__name__)

# Optional REST API (api.py). It is a no-op unless someone creates a token file, so an
# installation that does not want it is unaffected. See the "REST API" section in README.md.
limiter = None
try:
    from api import api_bp, register_limits

    app.register_blueprint(api_bp)

    # The limits api.py defines were never attached to anything: register_limits() had no
    # caller, so Flask-Limiter was installed and inert while README.md already promised
    # "10/minute per token, 30/minute overall" and a 429. Printing costs material, so an
    # unlimited endpoint is not academic.
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address

        # default_limits stays empty on purpose: only the API blueprint is capped. The kiosk
        # route /print must stay unlimited - README.md says so, and a queue at the printer is
        # not the place to discover a rate limit.
        limiter = Limiter(get_remote_address, app=app, default_limits=[],
                          storage_uri="memory://")
        register_limits(limiter)
    except ImportError:  # Flask-Limiter absent - the API works, just uncapped, as before
        pass
except ImportError:  # api.py removed or dependencies missing - the kiosk keeps working
    pass

# Optional printer simulator (simulator.py). Active when no printer is attached — an installation
# next to real hardware is byte-for-byte unaffected, the /sim routes then answer 404.
try:
    from simulator import sim_bp, sim_enabled, record_print

    app.register_blueprint(sim_bp)
except ImportError:  # simulator.py absent - hardware-only installation, as before
    def sim_enabled() -> bool:
        return False

    def record_print(image, name, subtitle, source):  # pragma: no cover - never called
        raise RuntimeError('simulator not available')

PRINTER_MODEL = 'QL-820NWB'
PRINTER_URI   = os.environ.get('PRINTER_URI', '/dev/usb/lp0')
LABEL_SIZE    = '62'

_hardware_da = None


def hardware_vorhanden(neu_pruefen: bool = False) -> bool:
    """Is there a real printer on this machine? Decided by the device node being there.

    CHECKED ONCE, AT STARTUP, AND THEN REMEMBERED — and that is the whole point of the flag.
    Checking per print would look more current and would be a trap: unplug the printer during an
    event and every following badge would quietly go to the simulator, with ``accepted`` in the
    response and nothing on paper. A machine that started with a printer stays a printing
    machine and reports an error when the device is gone. Being wrong loudly beats being wrong
    silently at a check-in desk.
    """
    global _hardware_da
    if _hardware_da is None or neu_pruefen:
        _hardware_da = os.path.exists(PRINTER_URI)
    return _hardware_da

SUBTITLES = [
    'Guild42.ch',
    'CH-Open.ch',
    'Workshop-Tage.ch',
]

RUNTIME_FILE = os.path.join(os.path.dirname(__file__), 'runtime.txt')


def get_default_subtitle():
    try:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        with open(env_path) as f:
            for line in f:
                if line.startswith('DEFAULT_SUBTITLE='):
                    return line.strip().split('=', 1)[1]
    except Exception:
        pass
    return 'Guild42.ch'


def get_active_subtitle():
    try:
        with open(RUNTIME_FILE) as f:
            value = f.read().strip()
            # Karte 1539: besides the three kiosk buttons, POST /api/v1/event (token only) may store
            # a free event title here - so any non-empty value counts, capped like every subtitle.
            if value:
                return value[:MAX_SUBTITLE]
    except Exception:
        pass
    return get_default_subtitle()


def set_active_subtitle(subtitle):
    with open(RUNTIME_FILE, 'w') as f:
        f.write(subtitle)


def _fitting_font(draw, text, path, start_size, min_size, max_width):
    """The largest size in ``[min_size, start_size]`` at which ``text`` fits into ``max_width``.

    WHY MEASURE INSTEAD OF COUNTING CHARACTERS: a character limit is a guess about width, and it
    is a bad one — "Willi" and "Wamburu" have the same length and nowhere near the same width at
    80px. The limit stays (it is what an operator can reason about at the kiosk), but the label
    itself is protected by the only thing that actually knows: the rendered width.

    Returns None when ``text`` is wider than ``max_width`` even at ``min_size``: the caller
    decides whether to re-flow (two lines) or to shrink below the readability floor — the one
    thing that must not happen is the name running off the paper (Karte 1629).
    """
    size = start_size
    while size >= min_size:
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return None


def _shrink_to_fit(draw, text, path, start_size, max_width):
    """The last resort below the readability floor: keeps shrinking until the text fits."""
    size = start_size
    while size > 0:
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return ImageFont.load_default()


FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

# Lesbarkeitsgrenzen des Schilds: darunter wird ein Name auf dem Label unleserlich. Die Fitting-
# Logik achtet sie nur, solange sie kann — ein zu langer Name landet unter der Grenze statt am
# rechten Rand abgeschnitten (Karte 1629).
NAME_MIN = 34
# Zwei Zeilen duerfen nie die Unterzeile (URL) erreichen: Darunter wird die Zeilenhoehe so gross,
# dass der Block bei der Zentrierung um y=120 die URL ueberlappt.
ZWEI_ZEILEN_MAX = 56


def _name_lines(draw, name, max_width):
    """How the name goes onto the label: Vorname and Nachname on a line of their own.

    Two lines are the default for every name with more than one word — the first word opens the
    first line, the rest goes below (Daniel, 02.11.2026). The break that lets both lines render
    the largest wins; its size is capped so the block keeps clear of the subtitle. A line that
    needs less than NAME_MIN first tries every other word break; only when no break fits does a
    line shrink below the readability floor — and even then it is fitted, never cut off.
    """
    woerter = name.split()
    if len(woerter) > 1:
        best = None
        for i in range(1, len(woerter)):
            eins = " ".join(woerter[:i])
            zwei = " ".join(woerter[i:])
            f1 = _fitting_font(draw, eins, FONT_BOLD, 80, NAME_MIN, max_width)
            f2 = _fitting_font(draw, zwei, FONT_BOLD, 80, NAME_MIN, max_width)
            if f1 is None or f2 is None:
                continue
            groesse = min(f1.size, f2.size, ZWEI_ZEILEN_MAX)
            if best is None or groesse > best[0]:
                best = (groesse, eins, zwei)
        if best is None:
            # Kein Umbruch passt in Lesbarkeitsgroesse: Vorname/Nachname trotzdem auf zwei Zeilen,
            # die zu breite Zeile schrumpft unter die Grenze — abgeschnitten wird nie.
            eins, zwei = woerter[0], " ".join(woerter[1:])
            f1 = _fitting_font(draw, eins, FONT_BOLD, 80, NAME_MIN, max_width) \
                or _shrink_to_fit(draw, eins, FONT_BOLD, NAME_MIN - 2, max_width)
            f2 = _fitting_font(draw, zwei, FONT_BOLD, 80, NAME_MIN, max_width) \
                or _shrink_to_fit(draw, zwei, FONT_BOLD, NAME_MIN - 2, max_width)
            best = (min(f1.size, f2.size), eins, zwei)
        groesse, eins, zwei = best
        return [(eins, ImageFont.truetype(FONT_BOLD, groesse)),
                (zwei, ImageFont.truetype(FONT_BOLD, groesse))]
    font = _fitting_font(draw, name, FONT_BOLD, 80, NAME_MIN, max_width)
    if font is not None:
        return [(name, font)]
    return [(name, _shrink_to_fit(draw, name, FONT_BOLD, NAME_MIN - 2, max_width))]


def create_label_image(name, subtitle='Guild42.ch'):
    W, H = 696, 271
    MARGIN = 20                       # ink this close to the edge is cut off by the roll
    max_width = W - 2 * MARGIN
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)
    font_sub = _fitting_font(draw, subtitle, FONT_REGULAR, 40, 18, max_width) \
        or _shrink_to_fit(draw, subtitle, FONT_REGULAR, 16, max_width)
    draw.rectangle([0, 0, W, 18], fill='#1a1a2e')
    lines = _name_lines(draw, name, max_width)
    if len(lines) == 1:
        draw.text((W // 2, 120), lines[0][0], font=lines[0][1], fill='black', anchor='mm')
    else:
        groesse = lines[0][1].size
        abstand = max(groesse // 3, 10)
        y = 120 - (groesse + abstand) // 2
        for text, font in lines:
            draw.text((W // 2, y), text, font=font, fill='black', anchor='mm')
            y += groesse + abstand
    draw.text((W // 2, 210), subtitle, font=font_sub, fill='#555555', anchor='mm')
    return img


# Label limits. They live HERE because they are a rendering constraint, not a transport one: one
# line, 80px font, 696px of label. api.py imports them instead of keeping its own copy — the two
# paths must not be able to produce labels the other cannot.
#
# WHY THIS IS NOT A COSMETIC REFACTOR: until now both files carried the number. When the kiosk went
# 40 -> 12 -> 15, api.py kept 40 and nothing said a word — a name printed through the API overflowed
# the paper while the same name was refused at the kiosk. One constant cannot drift from itself.
MAX_NAME = 15
MAX_SUBTITLE = 50


def max_name():
    """The character limit, overridable per installation via ``MAX_NAME``.

    WHY IT IS NOW A SETTING: guild42 makes the limit configurable per tenant, and a caller that
    is allowed to ask for 25 characters must not have them silently cut to 15 here. Read per call
    like the other .env-backed values, so no restart semantics need explaining. Long names no
    longer run off the paper either way — ``create_label_image`` scales the text down to fit.
    """
    try:
        wert = int(os.environ.get('MAX_NAME', MAX_NAME))
    except ValueError:
        return MAX_NAME
    return min(max(wert, 1), 40)


def _geclaimt() -> bool:
    try:
        from api import api_enabled, session_status as api_session_status
    except ImportError:
        return False
    return api_enabled() and api_session_status()["connected"]


@app.route('/')
def index():
    # get_active_subtitle() statt get_default_subtitle(): der zuletzt gesetzte Anlass gilt fuer die
    # ganze Laufzeit (upstream f8c829c). sim= bleibt daneben stehen, das eine sagt WELCHER Anlass,
    # das andere WOHIN gedruckt wird.
    # ?event=<title>: guild42 sends the desk here after onboarding, with the event chosen there.
    # It only changes THIS page (preview + the subtitle it sends), never the global runtime.txt -
    # a GET must not change state for everyone. No new exposure: /print already takes any subtitle.
    event = (request.args.get('event') or '').strip()[:MAX_SUBTITLE]
    # Karte 1557 (Daniel 21.10.2026): solange guild den Drucker geclaimt hat, ist die Startseite die
    # Nachmeldung des Anlasses - Gaeste am Eingang sollen nicht die Druckerseite sehen.
    if not event and _geclaimt():
        return nachmelden()
    return render_template('index.html',
                           default_subtitle=event or get_active_subtitle(),
                           event=event,
                           subtitles=SUBTITLES,
                           max_name=max_name(),
                           sim=sim_enabled())


@app.route('/set-event', methods=['POST'])
def set_event():
    # Karte 1466 L2: ein Body `null` oder kein Objekt ergab einen 500; jetzt 400 wie bei falschen Werten.
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    subtitle = (data.get('subtitle') or '').strip()
    if subtitle not in SUBTITLES:
        return jsonify({'error': 'Invalid subtitle'}), 400
    set_active_subtitle(subtitle)
    return jsonify({'ok': True, 'active': subtitle})


@app.route('/nachmelden')
def nachmelden():
    """Karte 1557: printer.guild42.ch/nachmelden leads to the late-registration form in guild.

    No login and no parameters passed on - the QR code at the desk only needs a stable address.
    """
    return redirect(os.environ.get('NACHMELDEN_URL', 'https://app.guild42.ch/nachmelden'), 302)


@app.route('/session-status')
def session_status():
    """Read-only status for the kiosk page: is an API session connected, and how much has it
    printed?

    Deliberately here and not on the API blueprint: the kiosk page has no token, so a status
    behind the API guard could not be displayed at all. It reveals nothing the kiosk does not
    already expose - anyone who can open this page can print on this printer - and it returns a
    boolean plus a counter, never the session token.

    Without api.py (or without a token file) it reports enabled=false and the page simply shows
    no status line.
    """
    try:
        from api import api_enabled, session_status as api_session_status
    except ImportError:
        return jsonify({'enabled': False})
    if not api_enabled():
        return jsonify({'enabled': False})
    return jsonify({'enabled': True, **api_session_status()})


@app.route('/session/freigeben', methods=['POST'])
def session_freigeben():
    """Admin release of the claim (Karte 1633, Daniel 02.11.2026).

    The admin container on the same host drops the claim WITHOUT the holder's token. Deliberately
    NOT under /admin/: nginx routes /admin/ to the admin container, so this endpoint lives next
    to /session-status on this app. Without a configured secret it answers 404 exactly like an
    unknown route; with one, the header must match. The claim is never released automatically —
    this endpoint and the holder's offboard are the only ways out.
    """
    geheim = os.environ.get('ADMIN_RELEASE_SECRET', '')
    if not geheim:
        return jsonify({'error': 'not_found'}), 404
    if not hmac.compare_digest(request.headers.get('X-Admin-Release', ''), geheim):
        app.logger.info('admin release rejected from %s', request.remote_addr)
        return jsonify({'error': 'unauthorized'}), 401
    try:
        from api import force_release
        freigegeben = force_release()
    except ImportError:
        freigegeben = None
    if freigegeben is None:
        return jsonify({'ok': True, 'released': False}), 200
    app.logger.info('session released via admin after %d prints (since %d)',
                    freigegeben['prints'], freigegeben['since'])
    return jsonify({'ok': True, 'released': True, **freigegeben}), 200


@app.route('/print', methods=['POST'])
def print_label():
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    name = (data.get('name') or '').strip()[:max_name()]
    subtitle = (data.get('subtitle') or get_active_subtitle()).strip()[:MAX_SUBTITLE]
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    try:
        img = create_label_image(name, subtitle)
        if sim_enabled():
            # Same pipeline, different sink: the label lands in the simulator instead of the
            # device. The response is identical on purpose - callers must not see a difference.
            record_print(img, name, subtitle, 'kiosk')
            return jsonify({'ok': True, 'name': name})
        qlr = BrotherQLRaster(PRINTER_MODEL)
        qlr.exception_on_warning = False
        convert(qlr=qlr, images=[img], label=LABEL_SIZE, rotate='auto', dpi_600=False)
        send(instructions=qlr.data,
             printer_identifier=PRINTER_URI,
             backend_identifier='linux_kernel')
        return jsonify({'ok': True, 'name': name})
    except Exception as e:
        # The exception text can contain device paths and internals; it goes to the log, not to
        # the caller. The UI only needs to know that printing failed.
        app.logger.exception('print failed: %s', e)
        return jsonify({'error': 'Printing failed'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
