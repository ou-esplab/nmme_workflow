def initPlotParams():
    import yaml
    from pathlib import Path

    # Load forecast regions from config (single source of truth)
    _cfg_path = Path(__file__).parent.parent / "confignmme.yaml"
    try:
        with open(_cfg_path) as _f:
            _cfg = yaml.safe_load(_f) or {}
        _cfg_regions = _cfg.get("regions", [])
    except Exception:
        _cfg_regions = []

    clevs_tas=[-4,-3,-2,-1,-0.5,-0.25,0.25,0.5,1,2,3,4]
    clevs_pr=[-10,-6,-4,-2,-1,-0.5,-0.25,0.25,0.5,1,2,4,6,10]
    clevs_zg=[-50,-45,-40,-35,-30,-25,-20,-15,15,20,25,30,35,40,45,50]
    clevs_sst=[-4,-3,-2,-1,-0.5,-0.25,0.25,0.5,1,2,3,4]

    # Region names from config — tref and prec plot for all forecast regions
    _region_names = [r["name"] for r in _cfg_regions]

    tas_dict={'name':'tref','plev':'2m','label':'2m Temperature','outname':'2mTemp',
              'clevs':clevs_tas,'cmap':'RdBu_r','units':'${^oC}$',
              'regions':['Global','NorthAmerica'] + _region_names,'scale_factor':1}
    pr_dict={'name':'prec','plev':'sfc','label':'Precipitation Rate','outname':'Precip',
              'clevs':clevs_pr,'cmap':'DryWet','units':'mm/day',
              'regions':['Global','NorthAmerica'] + _region_names,'scale_factor':1}
    zg_dict={'name':'zg','plev':'500',
             'label':'500hPa Geopotential Height',
             'outname':'500hPaGeopotentialHeight',
             'clevs':clevs_zg,'cmap':'NegPos','units':'m',
             'regions':['NorthernHemisphere'],'scale_factor':1}
    sst_dict={'name':'sst','plev':'sfc',
             'label':'Sea Surface Temperature',
             'outname':'SST',
             'clevs':clevs_sst,'cmap':'RdBu_r','units':'${^oC}$',
             'regions':['Global'],'scale_factor':1}

    var_params_dict=[tas_dict,pr_dict,sst_dict]

    # Built-in non-forecast regions (global/hemispheric backgrounds)
    global_dict={'name':'Global','lons':(0,360),'lats':(-90,90),'clon':0,'mproj':'robin',
                'state_colors':'gray5'}
    na_dict={'name':'NorthAmerica','lons':(190,305),'lats':(15,75),'clon':247.5,'mproj':'pcarree',
            'state_colors':'k'}
    nh_dict={'name':'NorthernHemisphere','lons':(-270,90),'lats':(30,90),'clon':247.5,'mproj':'npstere',
            'state_colors':'gray5'}

    reg_params_dict = [global_dict, na_dict, nh_dict]

    # Build forecast region dicts from config
    for r in _cfg_regions:
        lons = r["lon"]
        lats = r["lat"]
        plot = r.get("plot", {})
        # Convert negative longitudes to 0-360 for plotting
        lon0 = lons[0] if lons[0] >= 0 else lons[0] + 360
        lon1 = lons[1] if lons[1] >= 0 else lons[1] + 360
        reg_params_dict.append({
            "name":         r["name"],
            "lons":         (lon0, lon1),
            "lats":         (lats[0], lats[1]),
            "clon":         plot.get("clon", (lon0 + lon1) / 2),
            "mproj":        plot.get("mproj", "pcarree"),
            "state_colors": plot.get("state_colors", "gray"),
        })

    return var_params_dict, reg_params_dict
