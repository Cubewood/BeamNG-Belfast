import json, sys, time
sys.path.insert(0, r"H:\Claude\BeamNGBelfast\tools\spikes\gpu")
from lib import *
with SpikeLock():
    try:
        r = start_game(); print("ready", r["launch_to_ready_s"])
        c = client()
        print(gamectl.cmd_load_level("gridmap_v2"))
        print("install:", install_fps(c))
        print(measure_fps(c, 8, "normal-focused"))
        print(c.run_lua("settings.setValue('GraphicOverallQuality','Lowest') return 'ok'"))
        for i in range(4):
            time.sleep(2)
            print(c.run_lua("return table.concat({tostring(settings.getValue('GraphicOverallQuality')),tostring(settings.getValue('GraphicLightingQuality')),tostring(settings.getValue('GraphicShadowsQuality')),tostring(settings.getValue('GraphicTextureQuality')),tostring(settings.getValue('PostFXSSAOGeneralQuality'))},' ')"))
        print(measure_fps(c, 8, "lowest-focused"))
        defocus_game(); time.sleep(1)
        print("focused?", game_focused(c))
        print(measure_fps(c, 8, "lowest-unfocused"))
        print("vram", c.call_text("get_vram_usage"), vram_mib())
    finally:
        print(stop_game())
