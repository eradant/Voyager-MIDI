import time
import gc
import board
import busio
import rotaryio
import digitalio
import keypad
import usb_midi
import adafruit_midi
from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff
import adafruit_ssd1306
import neopixel

# ============================
# CONFIG & I2C INIT
# ============================
i2c = busio.I2C(board.SCL, board.SDA, frequency=1_000_000)

OLED_ADDR = 0x3C
# 128x64 rotated (rotation=3) becomes 64x128
oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c, addr=OLED_ADDR)
oled.rotation = 3 

# LED CONFIGURATION
NUM_PIXELS = 28
LED_PIN = board.D13
pixels = neopixel.NeoPixel(LED_PIN, NUM_PIXELS, brightness=1.0, auto_write=False, pixel_order=neopixel.GRB)

# Dim Base Colors (~0.2 brightness equivalent)
RAINBOW = [
    (50, 0, 0),   # Red
    (50, 20, 0),  # Orange
    (50, 50, 0),  # Yellow
    (0, 50, 0),   # Green
    (0, 50, 50),  # Cyan
    (0, 0, 50),   # Blue
    (30, 0, 50)   # Purple
]

BLUE_DIM = (0, 0, 50)
YELLOW_DIM = (50, 50, 0)
GREEN_DIM = (0, 50, 0)
BLUE_ROW_DIM = (0, 0, 50)

# ============================
# HARDWARE & MIDI
# ============================
midi = adafruit_midi.MIDI(midi_out=usb_midi.ports[1], out_channel=0)

ROW_PINS = (board.D10, board.D9, board.D6, board.D5)
COL_PINS = (board.SCK, board.D25, board.D24, board.A3, board.A2, board.A1, board.A0)

km = keypad.KeyMatrix(
    row_pins=ROW_PINS,
    column_pins=COL_PINS,
    columns_to_anodes=True,
    interval=0.002 
)

enc1 = rotaryio.IncrementalEncoder(board.MISO, board.MOSI)
sw1 = digitalio.DigitalInOut(board.D12)
sw1.switch_to_input(pull=digitalio.Pull.UP)

enc2 = rotaryio.IncrementalEncoder(board.RX, board.TX)
sw2 = digitalio.DigitalInOut(board.D11)
sw2.switch_to_input(pull=digitalio.Pull.UP)

# ============================
# DATA & STATE
# ============================
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MODES = {
    "Ionian": [0, 2, 4, 5, 7, 9, 11], "Dorian": [0, 2, 3, 5, 7, 9, 10],
    "Phrygian": [0, 1, 3, 5, 7, 8, 10], "Lydian": [0, 2, 4, 6, 7, 9, 11],
    "Mixolydian": [0, 2, 4, 5, 7, 9, 10], "Aeolian": [0, 2, 3, 5, 7, 8, 10],
    "Locrian": [0, 1, 3, 5, 6, 8, 10],
}
MODE_NAMES = list(MODES.keys())

MANUAL_MODS = {0: ("Maj", [4, 7]), 1: ("Min", [3, 7]), 2: ("7th", [4, 7, 10]), 3: ("Add9", [4, 7, 14]), 4: ("Sus4", [5, 7]), 5: ("Int2", [2]), 6: ("Int3", [4])}
SMART_MODS = {0: ("+7th", [6], [], {}), 1: ("+9th", [8], [], {}), 2: ("Sus4", [3], [2], {}), 3: ("Sus2", [1], [2], {}), 4: ("1Inv", [], [], {0: 1}), 5: ("2Inv", [], [], {0: 1, 2: 1}), 6: ("Sub", [], [], {0: -1})}

PLAY_MODE_MANUAL, PLAY_MODE_SMART = 0, 1
current_play_mode = PLAY_MODE_MANUAL
current_root_index, current_mode_index, octave_offset, velocity = 0, 0, 0, 100

last_enc1, last_sw1 = enc1.position, sw1.value
last_enc2, last_sw2 = enc2.position, sw2.value
enc2_turned_while_pressed, sw2_was_pressed = False, False

active_mods, active_notes = set(), {}         
ui_dirty = True
last_played_root, last_played_suffix, last_mod_str = "READY", "", ""
last_key_activity = 0.0 

# ============================
# HELPERS
# ============================
def get_led_index(row, col):
    """Maps 7x4 grid to snaking LED data line"""
    if row % 2 == 0: return (row * 7) + col
    else: return (row * 7) + (6 - col)

def boost_color(color_tuple):
    """0.2 -> 0.5 brightness boost for pressed keys"""
    return tuple(min(255, int(c * 2.5)) for c in color_tuple)

def get_idle_color(row, col):
    if current_play_mode == PLAY_MODE_MANUAL:
        if row == 0: return BLUE_ROW_DIM
        if row == 1: return GREEN_DIM
        if row == 2: return YELLOW_DIM
        return RAINBOW[col] # Row 3
    else:
        if row == 3: return BLUE_DIM
        return RAINBOW[col]

def init_led_background():
    for r in range(4):
        for c in range(7):
            pixels[get_led_index(r, c)] = get_idle_color(r, c)
    pixels.show()

def get_note_name(midi_num): return NOTE_NAMES[midi_num % 12]

def get_scale_note(row, col):
    base_octave_midi = 24 + ((2 - row) * 12) + (octave_offset * 12)
    return base_octave_midi + current_root_index + MODES[MODE_NAMES[current_mode_index]][col]

def get_diatonic_note(degree_index, oct_shift=0):
    base_octave_midi = 48 + (octave_offset * 12) + ((degree_index // 7) * 12) + (oct_shift * 12)
    return base_octave_midi + current_root_index + MODES[MODE_NAMES[current_mode_index]][degree_index % 7]

# ============================
# HANDLERS
# ============================
def handle_keys():
    global ui_dirty, last_played_root, last_played_suffix, last_mod_str, last_key_activity
    while True:
        event = km.events.get()
        if not event: break
        last_key_activity = time.monotonic()
        row, col = km.key_number_to_row_column(event.key_number)
        led_idx = get_led_index(row, col)
        
        if event.pressed:
            pixels[led_idx] = boost_color(get_idle_color(row, col))
            if row == 3:
                active_mods.add(col)
                mod_dict = MANUAL_MODS if current_play_mode == PLAY_MODE_MANUAL else SMART_MODS
                last_mod_str = "+".join([mod_dict.get(m, ("", []))[0] for m in sorted(active_mods)])
            else:
                notes_to_play, suffix_list = [], []
                if current_play_mode == PLAY_MODE_MANUAL:
                    base_note = get_scale_note(row, col)
                    intervals = {0}
                    for m in active_mods:
                        name, ivs = MANUAL_MODS.get(m, ("", []))
                        suffix_list.append(name); intervals.update(ivs)
                    notes_to_play = [base_note + i for i in intervals]
                    last_played_root = get_note_name(base_note)
                else:
                    if row < 2:
                        n = get_diatonic_note(col, oct_shift=(1 if row==0 else 0))
                        notes_to_play = [n]; last_played_root = get_note_name(n)
                    else:
                        degs = [0, 2, 4]
                        if active_mods:
                            m = min(active_mods)
                            name, add, rem, shft = SMART_MODS.get(m, ("", [], [], {}))
                            suffix_list.append(name); degs.extend(add)
                            degs = [d for d in degs if d not in rem]
                            notes_to_play = [get_diatonic_note(col+d, oct_shift=shft.get(d, 0)) for d in degs]
                        else:
                            notes_to_play = [get_diatonic_note(col+d) for d in degs]
                        bn = get_diatonic_note(col)
                        is_m = (get_diatonic_note(col+2)-bn)==3
                        is_dim = (get_diatonic_note(col+4)-bn)==6
                        last_played_root = f"{get_note_name(bn)}{'m' if is_m else ''}{'dim' if is_dim else ''}"
                for n in set(notes_to_play): midi.send(NoteOn(n, velocity))
                active_notes[event.key_number] = set(notes_to_play)
                last_played_suffix = "+".join(suffix_list)
            ui_dirty = True
        elif event.released:
            pixels[led_idx] = get_idle_color(row, col)
            if row == 3 and col in active_mods: active_mods.remove(col)
            if event.key_number in active_notes:
                for n in active_notes[event.key_number]: midi.send(NoteOff(n, 0))
                del active_notes[event.key_number]
        pixels.show()

def handle_encoders():
    global current_root_index, octave_offset, current_mode_index, velocity, current_play_mode
    global last_enc1, last_sw1, last_enc2, last_sw2, ui_dirty, sw2_was_pressed, enc2_turned_while_pressed
    changed = False
    
    ce1 = enc1.position
    if ce1 != last_enc1:
        delta = ce1 - last_enc1
        if not sw1.value: octave_offset = max(-2, min(2, octave_offset + delta))
        else: current_root_index = (current_root_index + delta) % 12
        last_enc1, changed = ce1, True

    if sw2.value != last_sw2:
        if not sw2.value: sw2_was_pressed, enc2_turned_while_pressed = True, False
        else:
            if sw2_was_pressed and not enc2_turned_while_pressed:
                current_play_mode = 1 - current_play_mode
                active_mods.clear()
                init_led_background()
            sw2_was_pressed = False
        last_sw2, changed = sw2.value, True

    ce2 = enc2.position
    if ce2 != last_enc2:
        delta = ce2 - last_enc2
        if not sw2.value: 
            velocity = max(0, min(127, velocity + (delta * 5)))
            enc2_turned_while_pressed = True
        else: current_mode_index = (current_mode_index + delta) % 7
        last_enc2, changed = ce2, True
    if changed: ui_dirty = True

def update_main_oled():
    oled.fill(0)
    mode_tag = "SMT" if current_play_mode == PLAY_MODE_SMART else "MAN"
    # Header
    oled.text(f"K:{NOTE_NAMES[current_root_index]} [{mode_tag}]", 0, 0, 1)
    oled.text(f"{MODE_NAMES[current_mode_index][:6]}", 0, 10, 1)
    oled.hline(0, 20, 64, 1)
    
    # Last Played Section
    oled.text("LAST:", 0, 25, 1)
    oled.text(last_played_root, 0, 36, 1, size=2)
    if last_played_suffix:
        oled.text(last_played_suffix[:10], 0, 56, 1)

    oled.hline(0, 80, 64, 1)
    
    # Footer Section
    if last_mod_str:
        oled.text("MODS:", 0, 86, 1)
        oled.text(last_mod_str[:10], 0, 96, 1)
    else:
        oled.text(f"OCT: {octave_offset}", 0, 90, 1)
        oled.text(f"VEL: {velocity}", 0, 102, 1)
    oled.show()

# ============= MAIN =============
init_led_background()
last_enc_time = time.monotonic()
while True:
    handle_keys()
    now = time.monotonic()
    if now - last_enc_time >= 0.01:
        handle_encoders()
        last_enc_time = now
    if ui_dirty and (now - last_key_activity > 0.1):
        update_main_oled()
        gc.collect()
        ui_dirty = False