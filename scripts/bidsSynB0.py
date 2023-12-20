#!/usr/bin/env python

import argparse
import bids
import json
import os
import shutil
import subprocess
import sys
import tempfile

def _filter_pybids_none_any(dct):
    import bids
    return {
        k: bids.layout.Query.NONE
        if v is None
        else (bids.layout.Query.ANY if v == "*" else v)
        for k, v in dct.items()
    }

def bids_filter(value):
    from bids.layout import Query

    if value and Path(value).exists():
        try:
            filters = loads(Path(value).read_text(), object_hook=_filter_pybids_none_any)
        except Exception as e:
            raise Exception("Unable to parse BIDS filter file. Check that it is "
                            "valid JSON.")
    else:
        raise Exception("Unable to load BIDS filter file " + value)

    # unserialize pybids Query enum values
    for acq, _filters in filters.items():
        filters[acq] = {
            k: getattr(Query, v[7:-4])
            if not isinstance(v, Query) and "Query" in v
            else v
            for k, v in _filters.items()
        }
    return filters

def get_dwi_images(layout, subject_label, session_label, filter_criteria):
    dwi_images = layout.get(subject=subject_label, session=session_label, suffix='dwi', extension=['nii.gz'], **filter_criteria)

    # Images grouped by acquisition
    # Run synb0 once per group, make an fmap with intendedfor all DWIs in that group
    grouped_images = {}

    for file in dwi_images:
        acq_label = file.get_entities()['acquisition']
        if not acq_label:
            acq_label = 'noacq'
        if acq_label not in grouped_images:
            grouped_images[acq_label] = []
        grouped_images[acq_label].append(file)

    return grouped_images


def get_t1w_skull_stripped(dataset, participant_label, session_label, t1w_filename):
    # Look in the mask dataset for a T1w mask matching the T1w image
    mask_dir = os.path.join(dataset, f"sub-{participant_label}", f"ses-{session_label}", 'anat')
    # Get all json files in the mask directory
    mask_sidecars = [f for f in os.listdir(mask_dir) if f.endswith('_mask.json')]

    t1w_mask = None
    t1w_skull_stripped_path = None

    for mask_sidecar in mask_sidecars:
        # Load the sidecar
        with open(os.path.join(mask_dir, mask_sidecar)) as json_file:
            mask_json = json.load(json_file)
            # Check if the T1w image matches the T1w image in the mask sidecar
            if mask_json['Sources'][0].endswith(t1w_filename):
                # Found a match
                t1w_mask = os.path.join(mask_dir, mask_sidecar.replace('.json', '.nii.gz'))
                break

    if t1w_mask is not None:
        print("Using T1w mask " + t1w_mask)
        t1w_skull_stripped_path = os.path.join(working_dir, "t1w_skull_stripped.nii.gz")
        t1w_is_skull_stripped = True
        subprocess.run(["fslmaths", t1w_path, "-mas", t1w_mask, t1w_skull_stripped_path])

    return t1w_skull_stripped_path



# parse arguments with argparse
# if no args, print usage
parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter,
                                 prog="bidsSynB0", add_help = False, description='''
Runs synb0 on BIDS data.

A BIDs filter can be used to select a subset of the data.

Limitations:

 * Only one T1w image per subject/session is supported. If more than one is found, the
script will exit.

 * Data must be organized as sub-<participant_label>/ses-<session_label>.

 * Axial scans are assumed, so that the phase encoding axis is AP (j, or j-) or LR (i or -i).
   Coronal scans should work if the PE is along RL or LR, but this is untested.


Requires: FSL, singularity

                                 ''')

required = parser.add_argument_group('Required arguments')
required.add_argument("-c", "--container", help="synb0 container to use", type=str, required=True)
required.add_argument("--bids-dataset", help="Input BIDS dataset", type=str, required=True)
required.add_argument("--participant-label", help="participant to process", type=str, required=True)
required.add_argument("--session-label", help="session to process", type=str, required=True)

optional = parser.add_argument_group('Optional arguments')
optional.add_argument("-h", "--help", action="help", help="show this help message and exit")
optional.add_argument("-f", "--filter", help="BIDS filter file", type=str, default=None)
optional.add_argument("-n", "--num-threads", help="Number of computational threads", type=int, default=1)
optional.add_argument("-t", "--total-readout-time", help="Total readout time for DWI. Some older DICOM files "
                      "do not provide this information, so it can be specified manually. Ignored if the BIDS "
                      "sidecar contains total readout time", type=str, default=None)
optional.add_argument("--combine-all-dwis", help="Combine all DWIs into one group. Useful for when the acq-label is not "
                      "sufficient to group scans", action='store_true')
optional.add_argument("--t1w-image-suffix", help="Use a specific T1w head image suffix. Eg, 'acq-mprage_T1w.nii.gz' selects "
                      "sub-participant/ses-session/sub-participant_ses-session_acq-mprage_T1w.nii.gz'. "
                      "Using this overrides BIDS filters for the T1w", type=str, default=None)
optional.add_argument("--t1w-mask-dataset", help="BIDS dataset to use for brain masking the T1w. If not specified, "
                      "synB0's internal algorithm is used for brain masking", type=str, default=None)
optional.add_argument("-w", "--work-dir", help="Temp dir for intermediate output, defaults to system "
                      "TMPDIR if defined, otherwise '/tmp'", type=str, default=os.environ.get('TMPDIR', '/scratch'))


args = parser.parse_args()

# open the BIDS dataset
layout = bids.BIDSLayout(args.bids_dataset, validate = True)

# Get filter if provided
filter = {}
if args.filter is not None:
    filter = bids_filter(args.filter)

# Get all dMRI data files for the given subject and session
dwi_groups = get_dwi_images(layout, args.participant_label, args.session_label, filter)

if (args.combine_all_dwis):
    # Combine all DWIs into one group - useful for when the acq-label is not consistent but
    # the phase encoding direction is - eg, HCP data with dir98 and dir99
    dwi_groups = {'combined': [item for sublist in dwi_groups.values() for item in sublist]}

if args.t1w_image_suffix is not None:
    t1w_files = [os.path.join(args.bids_dataset, f"sub-{args.participant_label}", f"ses-{args.session_label}", 'anat',
                f"sub-{args.participant_label}_ses-{args.session_label}_{args.t1w_image_suffix}")]
else:
    # return type files gets actual files not BIDSFile objects
    t1w_files = layout.get(subject=args.participant_label, session=args.session_label, suffix='T1w',
                           return_type='file', extension=['nii.gz'], **filter)

if (len(t1w_files) > 1):
    print("More than one T1w image found for subject " + args.participant_label + " session " + args.session_label +
          ". Need a more specific filter")
    sys.exit(1)

# Check t1w exists
if len(t1w_files) == 0:
    print("No T1w image found for subject " + args.participant_label + " session " + args.session_label)
    sys.exit(1)
if not os.path.exists(t1w_files[0]):
    print("T1w image " + t1w_files[0] + " not found")
    sys.exit(1)

t1w_path = t1w_files[0]

# Get filename from the full path
t1w_filename = os.path.basename(t1w_path)

# If this is None, use synb0's internal brain masking
t1w_skull_stripped_path = None
t1w_is_skull_stripped = False

# will be cleaned up after the script finishes
working_dir_tmpdir = tempfile.TemporaryDirectory(prefix=f"bids-synb0.", dir=args.work_dir, ignore_cleanup_errors=True)
working_dir = working_dir_tmpdir.name

# Print the files we're processing
print("Processing subject " + args.participant_label + " session " + args.session_label)
print("T1w: " + t1w_path)
print("DWI groups: " + str(dwi_groups))

if args.t1w_mask_dataset is not None:
    # Look in the mask dataset for a T1w mask matching the T1w image
    t1w_skull_stripped_path = get_t1w_skull_stripped(args.t1w_mask_dataset, args.participant_label, args.session_label,
                                                     t1w_filename)
else:
    # Search current dataset for a brain mask
    t1w_skull_stripped_path = get_t1w_skull_stripped(args.bids_dataset, args.participant_label, args.session_label,
                                                     t1w_filename)

if t1w_skull_stripped_path is not None:
    t1w_is_skull_stripped = True
else:
    print("No T1w brain mask found for subject " + args.participant_label + " session " + args.session_label)


# Run synb0 on each group of DWIs
for group in dwi_groups:
    print("Running synb0 on group " + group)
    # Check all images in the group have the same phase encoding direction
    pe_direction = None
    pe_direction_consistent = True
    group_dwi_images = dwi_groups[group]
    for dwi_image in group_dwi_images:
        print("Checking phase encoding direction for " + dwi_image.filename)
        # Get the phase encoding direction from the image metadata
        # Metadata is stored in the JSON sidecar file, same file name but with .json instead of
        # .nii.gz
        sidecar_file = dwi_image.path.replace('.nii.gz', '.json')
        with open(sidecar_file) as sidecar_fh:
            sidecar = json.load(sidecar_fh)
            if pe_direction is not None:
                if pe_direction != sidecar['PhaseEncodingDirection']:
                    print("Phase encoding direction for " +  dwi_image.filename + " does not match previous images in group")
                    pe_direction_consistent = False
            else:
                pe_direction = sidecar['PhaseEncodingDirection']

    if not pe_direction_consistent:
        print("Phase encoding direction not consistent for group " + group + ". Skipping group")
        continue

    print("Phase encoding direction for group is " + pe_direction)

    # Run synb0 on the group using the first b0

    dwi_ref = group_dwi_images[0]

    # Get total readout time from the first b0 or use command line alternative (needed for older DICOM files)
    total_readout_time = args.total_readout_time
    dwi_ref_sidecar_file = dwi_ref.path.replace('.nii.gz', '.json')

    # True if we need to add total readout time to the sidecar for all DWI images in the group
    # Without this, qsiprep won't be able to run SDC
    # Usually only for older data where the total readout time isn't in the sidecar
    dwi_needs_total_readout_time = False

    with open(dwi_ref_sidecar_file) as sidecar_fh:
        sidecar = json.load(sidecar_fh)
        try:
            total_readout_time = sidecar['TotalReadoutTime']
        except KeyError:
            dwi_needs_total_readout_time = True
            print("No total readout time in sidecar for " + dwi_ref.path)
            if total_readout_time is None:
                print("No total readout time in sidecar and no readout time specified on command line")
                sys.exit(1)

    if dwi_needs_total_readout_time:
        for dwi_image in group_dwi_images:
            sidecar_file = dwi_ref.path.replace('.nii.gz', '.json')
            print("Inserting total readout time from command line: " + total_readout_time)
            with open(sidecar_file) as sidecar_fh:
                sidecar = json.load(sidecar_fh)
                sidecar['TotalReadoutTime'] = total_readout_time
            with open(sidecar_file, 'w') as sidecar_fh:
                json.dump(fmap_sidecar, fmap_sidecar_fh, indent=2, sort_keys=True)


    # We need to put dir-<pe_direction> in the filename for the fmap/ to be BIDS compliant
    # The BIDS sidecar has letter codes i, i-, j, j-. I believe that k, k- are not supported by topup/eddy

    # for acqparams.txt, we need vectors in 3D
    phase_encode_vectors = {'i': [1, 0, 0], 'i-': [-1, 0, 0], 'j': [0, 1, 0], 'j-': [0, -1, 0], 'k': [0, 0, 1], 'k-': [0, 0, -1]}

    # for file names, we use letter labels. Note IS, SI are not supported
    phase_encode_labels = {'i': 'RL', 'i-': 'LR', 'j': 'PA', 'j-': 'AP'}

    synb0_env = os.environ.copy()
    synb0_env['SINGULARITYENV_TMPDIR'] = '/tmp'
    synb0_env['SINGULARITYENV_OMP_NUM_THREADS'] = str(args.num_threads)
    synb0_env['SINGULARITYENV_ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS'] = str(args.num_threads)

    # These are inputs and output for this group under the top working directory
    tmp_input_dir = os.path.join(working_dir, f"{group}_synb0_input")
    tmp_output_dir = os.path.join(working_dir, f"{group}_synb0_output")
    # Mount this to /tmp for the container
    tmp_singularity_dir = os.path.join(working_dir, f"{group}_synb0_tmpdir")

    os.makedirs(tmp_input_dir)
    os.makedirs(tmp_output_dir)
    os.makedirs(tmp_singularity_dir)

    # Get the first b0 image from the DWI
    b0_input = os.path.join(tmp_input_dir, 'b0.nii.gz')

    # fslroi exists 0 if the file doesn't exist, so check manually
    subprocess.run(['fslroi', dwi_ref.path, b0_input, '0', '1'], env=synb0_env)
    if not os.path.exists(b0_input):
        print("Could not extract b0 image from " + dwi_ref.path)
        sys.exit(1)

    t1_input = os.path.join(tmp_input_dir, 'T1.nii.gz')

    if t1w_is_skull_stripped:
        shutil.copy(t1w_skull_stripped_path, t1_input)
    else:
        shutil.copy(t1w_path, t1_input)

    # Not sure why it requires this if not running topup
    with open(os.path.join(tmp_input_dir, 'acqparams.txt'), 'w') as acqparams_fh:
        # write phase_encode_vectors['pe_direction']
        acqparams_fh.write(' '.join([str(x) for x in phase_encode_vectors[pe_direction]]) +
                            ' ' + str(total_readout_time) + '\n')
        acqparams_fh.write(' '.join([str(x) for x in phase_encode_vectors[pe_direction]]) +
                            ' ' + '0.000' + '\n')

    if shutil.which('singularity') is None:
        raise RuntimeError('singularity executable not found')

    # Get synb0 output and copy to fmap/
    synb0_cmd_list = ['singularity', 'run', '--cleanenv', '--no-home', '-B', f"{os.path.realpath(tmp_input_dir)}:/INPUTS",
                      '-B', f"{os.path.realpath(tmp_output_dir)}:/OUTPUTS",
                      '-B', f"{os.path.realpath(tmp_singularity_dir)}:/tmp",
                args.container, '--notopup']

    if t1w_is_skull_stripped:
        synb0_cmd_list.append('--stripped')

    print("---synb0 call---\n" + " ".join(synb0_cmd_list) + "\n---")

    subprocess.run(synb0_cmd_list, env=synb0_env)

    fmap_file_name = None

    fmap_phase_encode_dir = pe_direction[:-1] if pe_direction[-1] == '-' else pe_direction + '-'

    fmap_phase_encode_label = phase_encode_labels[fmap_phase_encode_dir]

    if group == 'noacq':
        fmap_file_name = f"sub-{args.participant_label}_ses-{args.session_label}_acq-synb0_dir-{fmap_phase_encode_label}_epi.nii.gz"
    else:
        fmap_file_name = f"sub-{args.participant_label}_ses-{args.session_label}_acq-{group}synb0_dir-{fmap_phase_encode_label}_epi.nii.gz"

    # output_dir is fmap/ under the session directory in the dataset
    output_dir = os.path.join(args.bids_dataset, f"sub-{args.participant_label}", f"ses-{args.session_label}", 'fmap')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    shutil.copy(os.path.join(tmp_output_dir, 'b0_u.nii.gz'), os.path.join(output_dir, fmap_file_name))

    fmap_sidecar_file = fmap_file_name.replace('.nii.gz', '.json')
    shutil.copy(dwi_ref_sidecar_file, os.path.join(output_dir, fmap_sidecar_file))

    # Edit the sidecar file to add the IntendedFor field, flip phase encode, and add echo spacing
    # and total readout time
    with open(os.path.join(output_dir, fmap_sidecar_file), 'r') as fmap_sidecar_fh:
        fmap_sidecar = json.load(fmap_sidecar_fh)
        fmap_intended_files = [os.path.join(f"ses-{args.session_label}", 'dwi', file.filename) for file in group_dwi_images]
        fmap_sidecar['IntendedFor'] = fmap_intended_files
        # flip pe, if i, set to i-, if i-, set to i
        fmap_sidecar['PhaseEncodingDirection'] = fmap_phase_encode_dir
        fmap_sidecar['TotalReadoutTime'] = 0.0000001
        fmap_sidecar['EffectiveEchoSpacing'] = 0.0

    with open(os.path.join(output_dir, fmap_sidecar_file), 'w') as fmap_sidecar_fh:
        json.dump(fmap_sidecar, fmap_sidecar_fh, indent=2, sort_keys=True)

