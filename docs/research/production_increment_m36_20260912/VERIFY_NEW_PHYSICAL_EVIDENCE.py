"""Only new physical-component checks on three received historical members.
No simulator, GDS write, parameter repair, label extraction, training or ledger.
Reuses existing foundry polygon/bridge functions; preserves historical schema.
"""
import hashlib, json, sys
from pathlib import Path
from datetime import datetime, timezone
import gdstk, yaml
from rfic_transformer_inverse_design.layout import foundry_audit as fa

def pin(p):
    p=Path(p); b=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
def read(p, expected=None):
    p=Path(p)
    if any(x.is_symlink() for x in (p,*p.parents)): raise ValueError("symlink")
    b=p.read_bytes()
    if expected and hashlib.sha256(b).hexdigest()!=expected: raise ValueError("input identity mismatch")
    return b
def save(p,v):
    with Path(p).open("x") as f: json.dump(v,f,indent=2,allow_nan=False); f.write("\n")
def actual_checks(previous, foundry, power, config):
    grid=config["emx"]["foundry_layout"]["manufacturing_grid_um"]
    if grid != .005: raise ValueError("current grid differs")
    gp=previous["actual_gds_copy"]
    read(gp["path"],gp["sha256"])
    lib=gdstk.read_gds(gp["path"]); tops=lib.top_level()
    if len(tops)!=1 or abs(lib.unit-1e-6)>1e-18: raise ValueError("top/unit mismatch")
    top=tops[0]
    polys=top.get_polygons(apply_repetitions=True,include_paths=True,depth=None)
    labels=top.get_labels(apply_repetitions=True,depth=None)
    groups={}
    for p in polys: groups.setdefault((p.layer,p.datatype),[]).append(p)
    b=power["shield_inner_bbox_um"]
    frame,recipe=fa._foundry_slotted_ground_frame(
        inner_bbox=tuple(b[k] for k in ("min_x_um","min_y_um","max_x_um","max_y_um")),
        frame_width_um=power["ground_frame_width_um"],
        strap_width_um=config["emx"]["foundry_layout"]["shield_strap_width_um"],
        strap_pitch_um=config["emx"]["foundry_layout"]["shield_strap_pitch_um"],
        manufacturing_grid_um=grid,layer=35,datatype=0)
    frame=fa._canonical_expected_polygons(frame,grid_um=grid)
    metal,vias=fa._expected_stitch_polygons(power,grid_um=grid)
    expected_m5=frame+metal.get((35,0),[])
    areas={"frame_missing_um2":fa._boolean_area(expected_m5,groups.get((35,0),[]),"not",grid),
           "frame_extra_um2":fa._boolean_area(groups.get((35,0),[]),expected_m5,"not",grid),
           "landing_missing_um2":0.0,"via_missing_um2":0.0,"via_extra_um2":0.0}
    for pair, expected in metal.items():
        areas["landing_missing_um2"]+=fa._boolean_area(expected,groups.get(pair,[]),"not",grid)
    for pair, records in vias.items():
        for expected, footprint in records:
            actual=groups.get(pair,[])
            areas["via_missing_um2"]+=fa._boolean_area(expected,actual,"not",grid)
            local=gdstk.boolean(actual,[footprint],"and",precision=grid*.1)
            areas["via_extra_um2"]+=fa._boolean_area(local,expected,"not",grid)
    bridges={k:fa._actual_bridge_record(foundry["power_line_bridge_connections"][k],groups,grid_um=grid)
             for k in ("primary_bridge","secondary_bridge")}
    rolemap=config["emx"]["power_line_8port"]["role_labels"]
    if power["labels"]!=rolemap: raise ValueError("source/current role map differs")
    ports=[]
    for i in range(1,5):
        name="P%03d"%i; primary=i<=2
        draw=(74,0) if primary else (39,60)
        mark=(126,0) if primary else (139,0)
        signal=[x for x in labels if x.text==name and (x.layer,x.texttype)==mark]
        ground=[x for x in labels if x.text==name+"_G" and (x.layer,x.texttype)==(135,0)]
        in_signal=bool(signal) and all(gdstk.inside([x.origin],groups.get(draw,[]))[0] for x in signal)
        in_ground=bool(ground) and all(gdstk.inside([x.origin],groups.get((35,0),[]))[0] for x in ground)
        sc={tuple(x.origin) for x in signal}; gc={tuple(x.origin) for x in ground}
        ports.append(dict(port=name,role="primary" if primary else "secondary",
            signal_drawing_pair=draw,pin_pair=mark,signal_label_coordinates=sorted(sc),
            ground_label_coordinates=sorted(gc),signal_label_on_own_conductor=in_signal,
            ground_label_on_m5=in_ground,signal_ground_colocated=sc==gc and bool(sc)))
    geometry=previous["geometry_parameter_check"]["geometry_um"]
    ranges=[]
    for field,value in geometry.items():
        if field=="line_width_um":
            intervals=[config["bounds"][s]["trace_width_um"] for s in ("primary","secondary")]
        elif field=="offset_um": intervals=[config["bounds"]["offset_um"]]
        else:
            side,tail=field.split("_",1)
            intervals=[config["bounds"][side][tail]]
        ranges.append(dict(field=field,value=value,bounds=intervals,pass_current_config=all(lo<=value<=hi for lo,hi in intervals)))
    checks=dict(ground_frame_matches_current_recipe=areas["frame_missing_um2"]<=fa.AREA_TOLERANCE_UM2 and areas["frame_extra_um2"]<=fa.AREA_TOLERANCE_UM2,
        vias_and_landing_match_bound_source=all(v<=fa.AREA_TOLERANCE_UM2 for k,v in areas.items() if not k.startswith("frame")),
        bridges_connected=all(v["overall_status"]=="PASS" for v in bridges.values()),
        port_role_labels_match_current=True,
        signal_and_ground_labels_on_intended_conductors=all(p["signal_label_on_own_conductor"] and p["ground_label_on_m5"] and p["signal_ground_colocated"] for p in ports),
        geometry_within_bound_current_config=all(x["pass_current_config"] for x in ranges))
    return dict(checks=checks,areas=areas,bridges=bridges,ports=ports,geometry_ranges=ranges,
                frame_recipe=recipe,actual_gds=gp,
                status="NEW_COMPONENT_CHECKS_PASS_NOT_FORMAL_CERTIFICATION" if all(checks.values()) else "CURRENT_COMPONENT_MISMATCH",
                limitations=["Port containment is not a complete EM process-byte identity or endpoint overlap proof.",
                    "Geometry tuple source hash namespace and full-history uniqueness are not certified here.",
                    "Original foundry metadata is retained; current recipe comparison is a new diagnostic, not a rewritten historical PASS."],
                original_split=previous["original_split"],formal_added=0)

def main():
    root,transport,prior=map(Path,sys.argv[1:4])
    manifest=json.loads(read(root/"READ_MANIFEST.json"))
    config_pin=next(x for x in manifest["files"] if x["role"]=="current_geometry_config")
    cfg=yaml.safe_load(read(transport/"current_geometry_config.yaml",config_pin["sha256"]))
    out=root/"actual_geometry_closure_v1"; out.mkdir(exist_ok=False)
    records=[]
    for ordinal in (1,2,11):
        fpin=next(x for x in manifest["files"] if x.get("ordinal")==ordinal and x["role"]=="foundry_layout_audit")
        ppin=next(x for x in manifest["files"] if x.get("ordinal")==ordinal and x["role"]=="power_line_geometry")
        fpath=transport/("authority_%03d_foundry_layout_audit.json"%ordinal)
        ppath=transport/("authority_%03d_power_line_geometry.json"%ordinal)
        foundry=json.loads(read(fpath,fpin["sha256"])); power=json.loads(read(ppath,ppin["sha256"]))
        oldpath=prior/("authority_%03d"%ordinal)/"RESULT.json"
        old=json.loads(read(oldpath))
        result=actual_checks(old,foundry,power,cfg)
        result.update(schema="eucap15_historical_three_new_physical_components.m36.v1",
            utc=datetime.now(timezone.utc).isoformat(),ordinal=ordinal,
            candidate_id_sha256=old["candidate_id_sha256"],
            prior_checks_reused=pin(oldpath),new_inputs=[pin(fpath),pin(ppath),pin(transport/"current_geometry_config.yaml")])
        dest=out/("authority_%03d.json"%ordinal);save(dest,result)
        records.append(dict(ordinal=ordinal,result=pin(dest),status=result["status"],checks=result["checks"],areas=result["areas"]))
    summary=dict(schema="eucap15_historical_three_new_physical_components_summary.m36.v1",records=records,
        implementation=pin(__file__),foundry_helper=pin(fa.__file__),source_manifest=pin(root/"READ_MANIFEST.json"),
        native_actions=0,formal_added=0,source_files_modified=False,old_checks_rerun=False,
        runtime_python=sys.executable,new_actual_members_checked=3)
    save(out/"SUMMARY.json",summary)
    print(json.dumps({"summary":pin(out/"SUMMARY.json"),"records":records}))
if __name__=="__main__": main()
