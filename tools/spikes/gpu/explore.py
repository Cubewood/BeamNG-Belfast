import json, sys, time
sys.path.insert(0, r"H:\Claude\BeamNGBelfast\tools\spikes\gpu")
from lib import *

with SpikeLock():
    try:
        r = start_game(); print("ready", r["launch_to_ready_s"], r["settings"] and "settings ok")
        c = client()
        print(gamectl.cmd_load_level("gridmap_v2"))
        print("preset before:", c.run_lua("return tostring(settings.getValue('GraphicOverallQuality'))"))
        print("metrics:", c.call_text("get_performance_metrics")[:3000])
        print("vram:", c.call_text("get_vram_usage"))
        print("fpslimit:", c.call_text("get_fps_limit_reason"))
        print(c.run_lua("settings.setValue('GraphicOverallQuality','Lowest') return tostring(settings.getValue('GraphicOverallQuality'))..' '..tostring(settings.getValue('GraphicLightingQuality'))..' '..tostring(settings.getValue('GraphicShadowsQuality'))"))
        time.sleep(5)
        print("metrics2:", c.call_text("get_performance_metrics")[:3000])
        print("vram2:", c.call_text("get_vram_usage"))
    finally:
        print(stop_game())
