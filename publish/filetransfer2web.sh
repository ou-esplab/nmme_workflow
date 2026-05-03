#!/bin/bash
set -xve
fcstdate=$1
sourceDirMon=/data/esplab/shared/model/initialized/nmme/forecast/monthly/$fcstdate
sourceDirSeas=/data/esplab/shared/model/initialized/nmme/forecast/seasonal/$fcstdate
sourceDirs=($sourceDirMon $sourceDirSeas)
destDir=/home/kpegion/http/nmme/forecasts/
destHost=somclass23.som.nor.ou.edu
execDir=/home/kpegion/projects/NMME/fcsts/src

# Activate conda environment
. /home/$USER/miniconda3/etc/profile.d/conda.sh
conda activate subxnmme


# Check if lock file exists if so, wait 1 min and check again
# Timeout after 1 hour

timeout=60
while [ $timeout > 0 ] && [ -f ${sourceDir}/nmmefcst.lock ]
do
  sleep 60
  ((timeout -= 1))
done

# If we didnt timeout then continue, otherwise exit with error
if [ $timeout >  0 ]
then

    # Create Forecast Date Directories on somclass

    ssh -i ~/.ssh/id_ed25519 ${destHost} "mkdir -p ${destDir}/images/${fcstdate}"
    ssh -i ~/.ssh/id_ed25519 ${destHost} "mkdir -p ${destDir}/data/${fcstdate}"

    # Copy images and data to appropriate directories on somclass

    for sourceDir in $sourceDirs
    do
       scp -i ~/.ssh/id_ed25519 ${sourceDir}/images/${fname}/* ${destHost}:${destDir}/images/${fcstdate}/
       scp -i ~/.ssh/id_ed25519 ${sourceDir}/data/${fname}/* ${destHost}:${destDir}/data/${fcstdate}/
       scp -i ~/.ssh/id_ed25519 ${sourceDir}/images/${fname}/* ${destHost}:${destDir}/images/Latest/
    done

    # Run Python Program to update html on somclass
    scp -i ~/.ssh/id_ed25519 ${destHost}:${destDir}/forecasts.html ./forecasts.${fcstdate}.html
    ./updatehtmldates.py --date ${fcstdate}
    scp -i ~/.ssh/id_ed25519 output.${fcstdate}.html ${destHost}:${destDir}/forecasts.html
    rm ./forecasts.${fcstdate}.html
    rm ./output.${fcstdate}.html
else

    echo "ERROR: makefcsts.sh did not complete successfully. Lock file still present for $fcstdate"
    exit 1 # terminate and indicate error

fi
