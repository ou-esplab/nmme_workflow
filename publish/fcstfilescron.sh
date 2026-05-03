#!/bin/bash
set -xve

execDIR=/home/kpegion/projects/NMME/fcsts/src/
prog=filetransfer2web.sh
fcstdate=$(date +%Y%m)

cd $execDIR
echo ${prog}
echo ${fcstdate}
echo $execDIR

./${prog} ${fcstdate} &> fcstfiles${fcstdate}.log
