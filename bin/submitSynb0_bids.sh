#!/bin/bash

module load miniconda/3-22.11
module load singularity/3.8.3
module load fsl/6.0.3

scriptPath=$(readlink -f "$0")
scriptDir=$(dirname "${scriptPath}")
# Repo base dir under which we find bin/, containers/ and scripts/
repoDir=${scriptDir%/bin}

function usage() {
  echo "Usage:
  $0 -v synb0_version -L logdir -- [options to bidsSynB0.py]

  $0 -h for help
  "
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

function help() {
cat << HELP
  `usage`

  This is a wrapper script to submit processing to bsub.

  bidsSynB0 options (note that the -c arg is provided automatically by this wrapper):

  `conda run -p /project/ftdc_pipeline/ftdc-picsl/miniconda/envs/ftdc-picsl-cp311 ${repoDir}/scripts/bidsSynB0.py -h`


HELP

}


while getopts "L:v:h" opt; do
  case $opt in
    L) logDir=$OPTARG;;
    v) synB0Version=$OPTARG;;
    h) help; exit 1;;
    \?) echo "Unknown option $OPTARG"; exit 2;;
    :) echo "Option $OPTARG requires an argument"; exit 2;;
  esac
done

shift $((OPTIND-1))

date=`date +%Y%m%d`

# Makes python output unbuffered, so we can tail the log file and see progress
# and errors in order
export PYTHONUNBUFFERED=1

if [[ -z ${logDir} ]]; then
    echo "Please specify a log directory with -L"
    exit 1
fi

if [[ ! -d ${logDir} ]]; then
    mkdir -p ${logDir}
fi

bsub -cwd . -o "${logDir}/synb0_${date}_%J.txt" \
    conda run -p /project/ftdc_pipeline/ftdc-picsl/miniconda/envs/ftdc-picsl-cp311 ${repoDir}/scripts/bidsSynB0.py \
      --container ${repoDir}/containers/synb0-${synB0Version}.sif $*
