#!/bin/bash

module load miniconda/3-22.11 > /dev/null 2>&1
module load singularity/3.8.3
module load fsl/6.0.3

fsLicense="/appl/freesurfer-7.1.1/license.txt"

scriptPath=$(readlink -f "$0")
scriptDir=$(dirname "${scriptPath}")
# Repo base dir under which we find bin/, containers/ and scripts/
repoDir=${scriptDir%/bin}

numThreads=1
synB0Version="3.0"

function usage() {
  echo "Usage:
  $0 -i bids_dataset [-n num_cores=1] [-s sessions.csv] [-v synb0_version=3.0] -- [options to bidsSynB0.py]

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

  Logs are written to the BIDS dataset under code/logs.

  To submit a single session, omit the -s option and specify the participant and session labels to the
  bitsSynB0.py script. For example, to process participant 01, session MR1:

    $0 -i /data/bids_dataset -n 2 -- --participant-label 01 --session-label MR1

  To submit multiple sessions, pass a CSV file with -s, where each line contains subjectID,sessionID. Each
  session will be submitted as a separate job.

  Script options:

    -i bids_dataset
       Path to BIDS dataset to process.

    -n num_cores
       Number of cores to use (default=${numThreads}).

    -s sessions.csv
       CSV file with subjectID,sessionID pairs to process. Each session will be submitted as a separate job.

    -v synb0_version
       Version of the synb0 container to use. This should match the version of
       the container in ${repoDir}/containers/synb0-${synB0Version}.sif (default=${synB0Version}).

  bidsSynB0 options are below. The following args are set automatically by this wrapper:

     -c / --container (set by -v in this wrapper)
     --bids-dataset (set by -i in this wrapper)
     -n / --num-threads (set by -n in this wrapper)
     --fs-license-file (hard-coded to ${fsLicense})

  `conda run -p /project/ftdc_pipeline/ftdc-picsl/miniconda/envs/ftdc-picsl-cp311 ${repoDir}/scripts/bidsSynB0.py -h`

HELP

}

while getopts "i:n:s:v:h" opt; do
  case $opt in
    i) inputBIDS=$OPTARG;;
    n) numThreads=$OPTARG;;
    s) sessionList=$OPTARG;;
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

logDir="${inputBIDS}/code/logs"

if [[ ! -d "${logDir}" ]]; then
  mkdir -p "${logDir}"
fi

echo

if [[ -f "${sessionList}" ]]; then
  while IFS=, read -r subject session; do
    echo "Submitting session ${subject},${session}"
    bsub -n $numThreads -cwd . -o "${logDir}/synb0_${date}_%J.txt" \
      conda run -p /project/ftdc_pipeline/ftdc-picsl/miniconda/envs/ftdc-picsl-cp311 ${repoDir}/scripts/bidsSynB0.py \
        --bids-dataset ${inputBIDS} \
        --container ${repoDir}/containers/synb0-${synB0Version}.sif \
        --num-threads $numThreads \
        --fs-license-file ${fsLicense} \
        --participant-label ${subject} \
        --session-label ${session} \
        $*
    sleep 0.5
  done < ${sessionList}
  exit 0
fi

echo "Submitting single session with args: $*"
echo

bsub -n $numThreads -cwd . -o "${logDir}/synb0_${date}_%J.txt" \
    conda run -p /project/ftdc_pipeline/ftdc-picsl/miniconda/envs/ftdc-picsl-cp311 ${repoDir}/scripts/bidsSynB0.py \
      --bids-dataset ${inputBIDS} \
      --container ${repoDir}/containers/synb0-${synB0Version}.sif \
      --num-threads $numThreads \
      --fs-license-file ${fsLicense} \
      $*
