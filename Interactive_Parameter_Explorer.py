import streamlit as st
import numpy as np
import pandas as pd
from scipy import signal
from bokeh.models import ColumnDataSource, LabelSet, HoverTool, Range1d
from bokeh.plotting import figure
from pyteomics import mzml, mass, parser
import requests
import io
from scipy.interpolate import interp1d

## FUNCTIONS ##

def peak_detection(spectrum, threshold=5, distance=4, prominence=0.8, width=2, centroid=False):
    relative_threshold = spectrum['intensity array'].max() * (threshold / 100)
    if centroid:
        peaks = np.where(spectrum['intensity array'] > relative_threshold)[0]
        return peaks
    else:
        peaks, properties = signal.find_peaks(spectrum['intensity array'], height=relative_threshold, prominence=prominence, width=width, distance=distance)
        return peaks, properties

def return_centroid(spectrum, peaks, properties):
    centroids = np.zeros_like(peaks, dtype='float32')
    for i, peak in enumerate(peaks):
        left_ip = int(properties['left_ips'][i])
        right_ip = int(properties['right_ips'][i])
        peak_range = range(left_ip, right_ip + 1)
        mz_range = spectrum['m/z array'][peak_range]
        intensity_range = spectrum['intensity array'][peak_range]
        centroids[i] = np.sum(mz_range * intensity_range) / np.sum(intensity_range)
    return centroids


def average_spectra(spectra, bin_width=None, filter_string=None):
    reference_scan = np.unique(spectra[0]['m/z array'])
    if bin_width is None:
        bin_width = np.min(np.diff(reference_scan))
    scan_min = spectra[0]['scanList']['scan'][0]['scanWindowList']['scanWindow'][0]['scan window lower limit']
    scan_max = spectra[0]['scanList']['scan'][0]['scanWindowList']['scanWindow'][0]['scan window upper limit']
    reference_mz = np.arange(scan_min, scan_max, bin_width)
    merge_intensity = np.zeros_like(reference_mz)

    for scan in spectra:
        tmp_mz = scan['m/z array']
        tmp_intensity = scan['intensity array']
        merge_intensity += np.interp(reference_mz, tmp_mz, tmp_intensity, left=0, right=0)

    merge_intensity = merge_intensity / len(spectra)

    avg_spec = spectra[0].copy()
    avg_spec['m/z array'] = reference_mz
    avg_spec['intensity array'] = merge_intensity
    avg_spec['scanList']['scan'][0]['filter string'] = "AV: {:.2f}-{:.2f}; {}".format(spectra[0]['scanList']['scan'][0]['scan start time'], spectra[-1]['scanList']['scan'][0]['scan start time'], filter_string)

    return avg_spec

def interpolate_spectra(spectra, target_energies, energies=[0, 5, 10, 15, 20]):
    intensity_arrays = [spectrum['intensity array'] for spectrum in spectra]
    interpolated_spectra = {target_energy: [] for target_energy in target_energies}

    for i in range(len(intensity_arrays[0])):
        intensities = [intensity_arrays[j][i] for j in range(len(energies))]
        
        for target_energy in target_energies:
            if target_energy < energies[0] or target_energy > energies[-1]:
                raise ValueError(f"Target energy {target_energy} is outside the range of energies in the spectra.")
            
            interpolated_intensity = np.interp(target_energy, energies, intensities)
            interpolated_spectra[target_energy].append(interpolated_intensity)
    
    return {k: np.array(v) for k, v in interpolated_spectra.items()}


aa_mass = mass.std_aa_mass
aa_mass['p'] = 79.966331  # phosphorylation (STY)
aa_mass['ox'] = 15.994915  # oxidation (MW)
aa_mass['d'] = 0.984016  # deamidation (NQ)
aa_mass['am'] = -0.984016  # amidation (C-term)

def get_fragments(sequence, fragment_ions, selected_charge_state):
    fragments = []
    _sequence = parser.parse(sequence)  # Assuming parser is defined somewhere

    for ion in fragment_ions:
        ion_type, pos = ion[0], int(ion[1:])
        if ion_type in ('a', 'b', 'c'):
            seq = ''.join(_sequence[:pos])
        else:
            seq = ''.join(_sequence[-pos:])
        
        # Calculate fragment mass
        _mass = mass.fast_mass2(seq, ion_type=ion_type, charge=selected_charge_state, aa_mass=aa_mass)
        
        # Determine ion label based on ion type
        if ion_type in ('a', 'b', 'c'):
            ion_label = ion_type + str(pos)
        elif ion_type in ('y', 'z', 'x'):
            ion_label = ion_type + str(len(_sequence) - pos + 1)
        else:
            ion_label = ion  # Handle any other types as they are
        
        fragments.append({'seq': seq, 'ion': ion_label, 'm/z': _mass, 'type': ion_type})

    return fragments


def load_predefined_data(peptide, charge_state, resolution, energy_ramp, isolation=None):
    file_map = {
        ('MRFA', '1+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_1%2B_collision_energy_ramp_enhanced_01.mzML',
        ('MRFA', '1+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_1%2B_collision_energy_ramp_turbo_01.mzML',
        ('MRFA', '1+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_1%2B_collision_energy_ramp_zoom_01.mzML',

        ('MRFA', '2+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_zoom_01.mzML',
        ('MRFA', '2+', 'Zoom', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_zoom_iso_2.mzML',
        ('MRFA', '2+', 'Zoom', 'Iso 3', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_zoom_iso_3.mzML',
        ('MRFA', '2+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_turbo_01.mzML',
        ('MRFA', '2+', 'Turbo', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_turbo_iso_2.mzML',
        ('MRFA', '2+', 'Turbo', 'Iso 3', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_turbo_iso_3.mzML',
        ('MRFA', '2+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_normal_01.mzML',
        ('MRFA', '2+', 'Normal', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_normal_iso_2.mzML',
        ('MRFA', '2+', 'Normal', 'Iso 3', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_normal_iso_3.mzML',
        ('MRFA', '2+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/05Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_enhanced_01.mzML',
        ('MRFA', '2+', 'Enhanced', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_enhanced_iso_2.mzML',
        ('MRFA', '2+', 'Enhanced', 'Iso 3', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/MRFA/12Mar2024_MJ_MRFA_2%2B_collision_energy_ramp_enhanced_iso_3.mzML',

        ('Bradykinin', '2+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_enhanced.mzML',
        ('Bradykinin', '2+', 'Enhanced', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_enhanced_defined.mzML',
        ('Bradykinin', '2+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_turbo.mzML',
        ('Bradykinin', '2+', 'Turbo', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_turbo_defined.mzML',
        ('Bradykinin', '2+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_zoom.mzML',
        ('Bradykinin', '2+', 'Zoom', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_zoom_defined.mzML',
        ('Bradykinin', '2+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_normal_01.mzML',
        ('Bradykinin', '2+', 'Normal', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_2%2B_collision_energy_ramp_normal_defined.mzML',
        ('Bradykinin', '3+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_enhanced.mzML',
        ('Bradykinin', '3+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_normal.mzML',
        ('Bradykinin', '3+', 'Normal', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_normal_defined.mzML',
        ('Bradykinin', '3+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_turbo.mzML',
        ('Bradykinin', '3+', 'Turbo', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_turbo_defined.mzML',
        ('Bradykinin', '3+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/13Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_zoom.mzML',
        ('Bradykinin', '3+', 'Zoom', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_zoom_defined.mzML',
        ('Bradykinin', '3+', 'Enhanced', 'Iso 1', 'Defined'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Bradykinin/19Mar2024_MJ_bradykinin_3%2B_collision_energy_ramp_enhanced_defined.mzML', 

        ('Substance_P', '2+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_2%2B_collision_energy_ramp_enhanced.mzML',
        ('Substance_P', '2+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_2%2B_collision_energy_ramp_normal.mzML',
        ('Substance_P', '2+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_2%2B_collision_energy_ramp_turbo.mzML',
        ('Substance_P', '2+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_2%2B_collision_energy_ramp_zoom.mzML',
        ('Substance_P', '3+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_3%2B_collision_energy_ramp_enhanced.mzML',
        ('Substance_P', '3+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_3%2B_collision_energy_ramp_normal.mzML',
        ('Substance_P', '3+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_3%2B_collision_energy_ramp_turbo.mzML',
        ('Substance_P', '3+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/Substance_P/20Mar2024_MJ_subp_3%2B_collision_energy_ramp_zoom.mzML',
        
        ('GRGDS', '1+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_enhanced.mzML',
        ('GRGDS', '1+', 'Enhanced', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_enhanced_02.mzML',
        ('GRGDS', '1+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_normal.mzML',
        ('GRGDS', '1+', 'Normal', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_normal_02.mzML',
        ('GRGDS', '1+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_turbo.mzML',
        ('GRGDS', '1+', 'Turbo', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_turbo_02.mzML',
        ('GRGDS', '1+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_zoom.mzML',
        ('GRGDS', '1+', 'Zoom', 'Iso 2', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_1%2B_collision_energy_ramp_zoom_02.mzML',
        ('GRGDS', '2+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_2%2B_collision_energy_ramp_enhanced.mzML',
        ('GRGDS', '2+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_2%2B_collision_energy_ramp_normal.mzML',
        ('GRGDS', '2+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_2%2B_collision_energy_ramp_turbo.mzML',
        ('GRGDS', '2+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/GRGDS/21Mar2024_MJ_GRGDS_2%2B_collision_energy_ramp_zoom.mzML',

        ('SDGRG', '1+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_1%2B_collision_energy_ramp_enhanced.mzML',
        ('SDGRG', '1+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_1%2B_collision_energy_ramp_normal.mzML',
        ('SDGRG', '1+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_1%2B_collision_energy_ramp_turbo.mzML',
        ('SDGRG', '1+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_1%2B_collision_energy_ramp_zoom.mzML',
        ('SDGRG', '2+', 'Enhanced', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_2%2B_collision_energy_ramp_enhanced.mzML',
        ('SDGRG', '2+', 'Normal', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_2%2B_collision_energy_ramp_normal.mzML',
        ('SDGRG', '2+', 'Turbo', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_conc_2%2B_collision_energy_ramp_turbo.mzML',
        ('SDGRG', '2+', 'Zoom', 'Iso 1', 'Centre'): 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Data/SDGRG/21Mar2024_MJ_SDGRG_conc_2%2B_collision_energy_ramp_zoom.mzML',

    }

    if isolation is not None and (peptide == "Bradykinin"):
        selected_file_url = file_map.get((peptide, charge_state, resolution, energy_ramp, isolation))
    else: 
        selected_file_url = file_map.get((peptide, charge_state, resolution, energy_ramp, "Centre"))


    if selected_file_url:
        response = requests.get(selected_file_url)
        raw_data = io.BytesIO(response.content)
        reader = mzml.read(raw_data, use_index=True)
        

        scan_energy_list = {}
    reader.reset()
    for scan in reader:
        idx = scan['index']
        if scan['ms level'] == 1:
            continue  # Skip MS1 scans
        if 'precursorList' in scan and 'precursor' in scan['precursorList'] and len(scan['precursorList']['precursor']) > 0:
            if 'activation' in scan['precursorList']['precursor'][0] and 'collision energy' in scan['precursorList']['precursor'][0]['activation']:
                energy = scan['precursorList']['precursor'][0]['activation']['collision energy']
                if energy not in scan_energy_list:
                    scan_energy_list[energy] = []
                scan_energy_list[energy].append(idx)
    
    return reader, scan_energy_list
    

@st.cache_data
def load_data(raw_file):
    reader = mzml.read(raw_file, use_index=True)
    scan_energy_list = {}
    reader.reset()
    for scan in reader:
        idx = scan['index']
        if scan['ms level'] == 1:
            continue  # Skip MS1 scans
        if 'precursorList' in scan and 'precursor' in scan['precursorList'] and len(scan['precursorList']['precursor']) > 0:
            if 'activation' in scan['precursorList']['precursor'][0] and 'collision energy' in scan['precursorList']['precursor'][0]['activation']:
                energy = scan['precursorList']['precursor'][0]['activation']['collision energy']
                if energy not in scan_energy_list:
                    scan_energy_list[energy] = []
                scan_energy_list[energy].append(idx)
    
    return reader, scan_energy_list

 ## APP LAYOUT ##

st.set_page_config(page_title="Interactive mzML Parameter Explorer", layout="wide", menu_items={'about': "This application is a parameter explorer for mzML mass spectrometry data. Written by Kiah Slater."})
st.sidebar.title("Interactive mzML Parameter Explorer")
st.sidebar.markdown("This is an interactive parameter explorer for mass spectrometry data stored in .mzML data format")

peptide_options = {
    "MRFA": {
        "charge_states": ["1+", "2+"],
        "resolutions": {
            "1+": ["Enhanced", "Turbo", "Zoom"],
            "2+": ["Enhanced", "Normal", "Turbo", "Zoom"]
        },
        "energy_ramps": {
            "1+": ["Iso 1"],
            "2+": ["Iso 1", "Iso 2", "Iso 3"]
        }
    },
    "GRGDS": {
        "charge_states": ["1+", "2+"],
        "resolutions": ["Normal", "Turbo", "Zoom", "Enhanced"],
        "energy_ramps": {
            "1+": ["Iso 1", "Iso 2"],
            "2+": ["Iso 1"]
        }
    },
    "SDGRG": {
        "charge_states": ["1+", "2+"],
        "resolutions": ["Enhanced", "Normal", "Turbo", "Zoom"],
        "energy_ramps": ["Iso 1"]
    },
    "Bradykinin": {
        "charge_states": ["2+", "3+"],
        "resolutions": ["Enhanced", "Normal", "Turbo", "Zoom"],
        "energy_ramps": ["Iso 1"]
    },
    "Substance_P": {
        "charge_states": ["2+", "3+"],
        "resolutions": ["Enhanced", "Normal", "Zoom", "Turbo"],
        "energy_ramps": ["Iso 1"]
    }
}

# Streamlit sidebar widgets
use_predefined_data = st.sidebar.checkbox("Use Predefined Data", value=True, help="Toggle to use predefined data")
selected_peptide = st.sidebar.selectbox("Select Peptide", list(peptide_options.keys()))

def get_options(selected_peptide, option_type):
    return peptide_options[selected_peptide][option_type]

selected_charge_state = st.sidebar.selectbox("Select Charge State", get_options(selected_peptide, "charge_states"))

def get_resolutions(selected_peptide, selected_charge_state):
    if selected_peptide == "MRFA":
        if selected_charge_state in peptide_options[selected_peptide]["resolutions"]:
            return peptide_options[selected_peptide]["resolutions"][selected_charge_state]
        else:
            return []  # Return an empty list if no valid options are found
    else:
        return peptide_options[selected_peptide]["resolutions"]

selected_resolution = st.sidebar.selectbox("Select Resolution", get_resolutions(selected_peptide, selected_charge_state))

def get_energy_ramp_options(selected_peptide, selected_charge_state):
    if selected_peptide in peptide_options and selected_charge_state in peptide_options[selected_peptide]["energy_ramps"]:
        return peptide_options[selected_peptide]["energy_ramps"][selected_charge_state]
    else:
        return ["Iso 1"]  # Default option if no valid options are found

# Selectbox for choosing energy collision ramp
selected_energy_ramp_options = get_energy_ramp_options(selected_peptide, selected_charge_state)
if selected_energy_ramp_options:
    selected_energy_ramp = st.sidebar.selectbox("Select Energy Collision Ramp", selected_energy_ramp_options)
else:
    selected_energy_ramp = None

# Conditionally show isolation selectbox based on selected peptide
if use_predefined_data and selected_peptide == "Bradykinin":
    isolation_options = ["Centre", "Defined"]
    selected_isolation = st.sidebar.selectbox("Select Isolation", isolation_options)
else:
    selected_isolation = None


spectrum_tab, instructions_tab = st.tabs(["Spectrum", "Instructions"])


with instructions_tab:  
    
    image_predefined_data = 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Instruction%20images/Data%20Selection.png'
    image_user_data = 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Instruction%20images/Drag%20and%20drop%20data.png'
    image_parameter_selection = 'https://github.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/raw/main/Instruction%20images/Parameter%20selection%20.png'
    image_setting_selection = 'https://github.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/raw/main/Instruction%20images/Settings%20selection%20.png'
    image_plot_expansion = 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Instruction%20images/View%20fullscreen.png'
    image_zoom_function =  'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Instruction%20images/Zoom%20function.png'
    image_hover_function = 'https://raw.githubusercontent.com/KSlater14/Interactive-Tandem-Mass-Spectrometry-App-/main/Instruction%20images/Hover%20function.png'

    st.header("Instructions")
    st.markdown("Instructions for the use of the Interactive Parameter Explorer")
    st.subheader("Data Selection")
    st.write("""
- To begin the user can use either the predefined data by selecting the toggle box or 
             can use their own data in the MZML format via the drag and drop box. 
- Once the data has been selected, the spectra will automatically be plotted.
             """)
    
    col1, col2 = st.columns(2)
    with col1: 
        st.image(image_predefined_data, caption='Data Selection: Using data already available', width=300)
    with col2:
        st.image(image_user_data, caption='Data Selection: User utilising their own data', width=300)

    st.subheader("Changing the parameters")
    st.write("""
    - Both the settings and parameters can be changed. 
    - The parameters can be changed via the sidebar, 
             which allows the peptide, charge state, resolution, energy ramp and isolation to be changed for the predefined data. """)
    st.image(image_parameter_selection, caption='Parameter Selection', width=300)
    ("""
    - The changing of parameters via the sidebar is not applicable if the user choses to upload their own data. 
    - Once the parameters have been selected or the users data inserted, the settings of the plot can be altered. 
    - The Settings:
        - Allows for a collision induced dissociation (CID) energy to be selected, which displays the spectrum with the changing collision energy. 
        - Allows for m/z labels to be selected to label the spectrum. The threshold of these labels can be selected too.
        - Checkbox can be selected to annotate the fragments with the ions within the spectrum. 
             """)
    st.image(image_setting_selection, caption='The settings available for selection', width=300)
    
    st.subheader("Plot interactivity")
    st.write(""" 
    - Various features allow for the plot to be explored. These features include:

        - The expansion of the plot to a full screen.""")
    st.image(image_plot_expansion, caption='Button for spectrum plot expansion', width=800)
    ("""
        - The ability to drag the cursor of a section of the plot to zoom in for exploration.""")
    st.image(image_zoom_function, caption='The cursor drag zoom function', width=600)
    ("""
        - A hover tool allows the cursor, when hovering over a peak, to display the m/z, intensity and centroid data regarding each peak.
             """)
    st.image(image_hover_function, caption='The hover function in use', width=600)
    

with spectrum_tab:
    st.markdown("Explore the parameters influencing the spectra, over a series of scans. Select instructions tab for help.")


# Load predefined data based on selected parameters
if use_predefined_data:
    reader, scan_filter_list = load_predefined_data(selected_peptide, selected_charge_state, selected_resolution, selected_energy_ramp, isolation=None)
else:
    raw_file = st.sidebar.file_uploader("Choose a file", type=['mzml'], key="rawfile", help="Choose an mzML file for exploration.")
    if raw_file is not None:
        reader, scan_filter_list = load_data(raw_file)
    else: 
        reader = None

# Initialize the labels_on variable to True
labels_on = True 
label_ions = True

# Streamlit layout
with spectrum_tab: 
    scol1, scol2 = st.columns([0.3, 0.7])
    with scol1:
        if reader is not None:
            st.markdown("### Settings")
            _available_energies = [0, 5, 10, 15, 20]
            available_energies = [e for e in _available_energies if e in scan_filter_list]
            scan_filter = st.number_input("Select Collision Energy", min_value=available_energies[0], max_value=available_energies[-1], value=10, step=1, help="Filter scans by collision energy.")

            if scan_filter in scan_filter_list:
                scan_range = (scan_filter_list[scan_filter][0], scan_filter_list[scan_filter][-1])
                spectra = [reader[i] for i in range(scan_range[0], scan_range[1] + 1)]
                selected_scan = average_spectra(spectra, filter_string=scan_filter)
            else:
                spectra = [average_spectra(reader[scan_filter_list[energy]]) for energy in available_energies]
                interpolated_spectra = interpolate_spectra(spectra, [scan_filter], energies=available_energies)
                selected_scan = {
                    'm/z array': spectra[0]['m/z array'],
                    'intensity array': interpolated_spectra[scan_filter],
                    'scanList': {'scan': [{'scanWindowList': {'scanWindow': [{'scan window lower limit': min(spectra[0]['m/z array']),
                                                                             'scan window upper limit': max(spectra[0]['m/z array'])}]}}]}
                }

            label_threshold = st.number_input("Label Threshold (%)", min_value=0, value=2, help="Label peaks with intensity above threshold% of maximum.")
            labels_on = st.checkbox("Show m/z labels", help="Display all peak labels on plot.", value=True)
            label_ions = st.checkbox("Annotate Spectrum", help="Display all fragment labels on plot", value=True)
            
            # Plot spectrum function
            def plot_spectrum(selected_scan, labels_on, label_ions, selected_peptide):
                spectrum_plot = figure(
                x_axis_label='m/z',
                y_axis_label='intensity',
                tools='pan,box_zoom,xbox_zoom,reset,save',
                active_drag='xbox_zoom'
    )

                spectrum_plot.left[0].formatter.use_scientific = True
                spectrum_plot.left[0].formatter.power_limit_high = 0
                spectrum_plot.left[0].formatter.precision = 1
                spectrum_plot.y_range.start = 0

                if 'scanList' in selected_scan and 'scan' in selected_scan['scanList'] and len(selected_scan['scanList']['scan']) > 0:
                    if 'scanWindowList' in selected_scan['scanList']['scan'][0]:
                        min_mz = selected_scan['scanList']['scan'][0]['scanWindowList']['scanWindow'][0]['scan window lower limit']
                        max_mz = selected_scan['scanList']['scan'][0]['scanWindowList']['scanWindow'][0]['scan window upper limit']
                        spectrum_plot.x_range = Range1d(min_mz, max_mz, bounds="auto")

                spectrum_plot.line(selected_scan['m/z array'], selected_scan['intensity array'], line_width=2, color='black')

    # Peak detection and centroid calculation
                _peaks, _properties = peak_detection(selected_scan, threshold=5, centroid=False)
                _peak_centroids = return_centroid(selected_scan, _peaks, _properties)

    # Create ColumnDataSource for peaks
                peaks_data = {
                    'x': selected_scan['m/z array'][_peaks],
                    'y': selected_scan['intensity array'][_peaks],
                    'cent': ["%.2f" % x for x in _peak_centroids]
    }
                peaks_source = ColumnDataSource(data=peaks_data if _peaks.size > 0 else {'x': [], 'y': [], 'cent': []})
                r = spectrum_plot.circle('x', 'y', size=5, source=peaks_source, color='red')

    # Hover tool configuration
                hover = HoverTool(tooltips=[
                    ("m/z", "@x{0.00}"),
                    ("intensity", "@y{0.0}"),
                    ("centroid", "@cent{0.00}")
                ], renderers=[r])
                spectrum_plot.add_tools(hover)

    # Conditionally add labels if labels_on is True
                if labels_on:
                    labels = LabelSet(x='x', y='y', text='cent', source=peaks_source, text_font_size='8pt', text_color='black')
                    spectrum_plot.add_layout(labels)

                if label_ions and selected_peptide:
                    try:
                        cleaned_charge_state = int(selected_charge_state.rstrip('+'))  # Remove '+' and convert to integer

    # Use get_fragments to calculate fragment m/z values
                        fragment_ions = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'c1', 'c2', 'c3', 'c4', 'x1', 'x2', 'x3', 'x4', 'y1', 'y2', 'y3', 'y4', 'z1', 'z2', 'z3', 'z4']  
                        fragments = get_fragments(selected_peptide, fragment_ions, cleaned_charge_state)
                           
                        # Annotate spectrum with theoretical fragments
                        ions_data = {
                            'x': [frag['m/z'] for frag in fragments],
                            'y': [selected_scan['intensity array'][np.argmin(np.abs(selected_scan['m/z array'] - frag['m/z']))] * 1.05 for frag in fragments],
                            'ion_type': [frag['ion'] for frag in fragments]
                        }
                        ions_source = ColumnDataSource(data=ions_data)

                        ion_labels = LabelSet(x='x', y='y', text='ion_type', source=ions_source, text_font_size='8pt', text_color='blue', y_offset=8)
                        spectrum_plot.add_layout(ion_labels)

                    except ValueError as ve:
                        print(f"Error: Invalid precursor charge value '{selected_charge_state}'. {ve}")
                    except Exception as e:
                        print(f"Error annotating spectrum with peptide: {selected_peptide}. Error: {e}")

                    return spectrum_plot

    with scol2:
        if reader is not None:
            spectrum_plot = plot_spectrum(selected_scan, labels_on, label_ions, selected_peptide)
            st.bokeh_chart(spectrum_plot, use_container_width=True)


