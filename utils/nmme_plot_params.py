def initPlotParams():

    # Dictionary defining parameters for plottting variables

    clevs_tas=[-4,-3,-2,-1,-0.5,-0.25,0.25,0.5,1,2,3,4]
    #clevs_pr=[-100,-50,-25,-10,-5,-2,2,5,10,25,50,100]
    clevs_pr=[-10,-6,-4,-2,-1,-0.5,-0.25,0.25,0.5,1,2,4,6,10]
    clevs_zg=[-50,-45,-40,-35,-30,-25,-20,-15,15,20,25,30,35,40,45,50]
    clevs_sst=[-4,-3,-2,-1,-0.5,-0.25,0.25,0.5,1,2,3,4]

    tas_dict={'name':'tref','plev':'2m','label':'2m Temperature','outname':'2mTemp',
              'clevs':clevs_tas,'cmap':'RdBu_r','units':'${^oC}$',
              'regions':['Global','NorthAmerica'],'scale_factor':1}
    pr_dict={'name':'prec','plev':'sfc','label':'Precipitation Rate','outname':'Precip',
              'clevs':clevs_pr,'cmap':'DryWet','units':'mm/day',
              'regions':['Global','NorthAmerica','Iran','Venezuela'],'scale_factor':1}
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

    # Dictionary defining parameters for plotting different regions

    global_dict={'name':'Global','lons':(0,360),'lats':(-90,90),'clon':0,'mproj':'robin',
                'state_colors':'gray5'}
    na_dict={'name':'NorthAmerica','lons':(190,305),'lats':(15,75),'clon':247.5,'mproj':'pcarree',
            'state_colors':'k'}
    nh_dict={'name':'NorthernHemisphere','lons':(-270,90),'lats':(30,90),'clon':247.5,'mproj':'npstere',
            'state_colors':'gray5'}
    iran_dict={'name':'Iran','lons':(40,64),'lats':(24,40),'clon':52,'mproj':'pcarree','state_colors':'gray'}
    venezuela_dict={'name':'Venezuela','lons':(287,300),'lats':(0,13),'clon':293.5,'mproj':'pcarree','state_colors':'gray'}
    mexico_dict={'name':'Mexico','lons':(242,263),'lats':(20,33),'clon':252.5,'mproj':'pcarree','state_colors':'gray'}

    reg_params_dict=[global_dict,na_dict,nh_dict,iran_dict,venezuela_dict,mexico_dict]

    return var_params_dict, reg_params_dict
