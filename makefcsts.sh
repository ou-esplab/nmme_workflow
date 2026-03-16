#!/bin/bash
set -xve

# Load python module to get access to conda
#. /usr/share/Modules/init/bash
#module load anaconda/3

# Activate conda environment
. /home/kpegion/miniconda3/etc/profile.d/conda.sh
conda activate subxnmme

# Confirm conda and python location (for testing purposes)
which conda
which python
echo $CONDA_DEFAULT_ENV

# Get the fcstdate provided as command line argument
fcstdate=$1
echo ${fcstdate}

# Set Output Path

outPath1=/data/esplab/shared/model/initialized/nmme/forecast/monthly/
outPath2=/data/esplab/shared/model/initialized/nmme/forecast/seasonal/

# Make directories for this forecast if they don't exist
if [ ! -d "${outPath1}/$fcstdate/images/" ]
then
   echo "Making Directory ${outPath1}/$fcstdate/images/"
   mkdir -p ${outPath1}/$fcstdate/images
   echo "Making Directory ${outPath2}/$fcstdate/images/"
   mkdir -p ${outPath2}/$fcstdate/images
fi
if [ ! -d "${outPath1}/$fcstdate/data/" ]
then
   echo "Making Directory ${outPath1}/$fcstdate/data/"
   mkdir -p ${outPath1}/$fcstdate/data
   echo "Making Directory ${outPath2}/$fcstdate/data/"
   mkdir -p ${outPath2}/$fcstdate/data
fi

# Create lock file in this fcsts directory
touch ${outPath1}/$fcstdate/nmmefcst.lock
touch ${outPath2}/$fcstdate/nmmefcst.lock

# Run Program to Make Forecast plot and data files
./MakeNMMEFcsts.py --date ${fcstdate} 

# Remove lockfiles if this program runs to completion
rm ${outPath1}/$fcstdate/nmmefcst.lock
rm ${outPath2}/$fcstdate/nmmefcst.lock

