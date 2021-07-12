# 88255

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from random import randint
from statistics import median
from math import sqrt
from typing import List, Callable, Iterable

import clipboard
import dpkt
import geopandas as gpd
import pandas
from matplotlib import pyplot as plt
from shapely.ops import nearest_points

sys.path.append('../')  # FIXME

PATH_TO_CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
TSHARK = '/Applications/Wireshark.app/Contents/MacOS/tshark'
TMP_CHROME_PROFILE = '/Users/eab06/Desktop/WJB/PythonProjects/HT_Data/src/GetAllDoctors/DetermineTEDSpecialists/__TMP_CHROME_PROFILE'
TMP_CHROME_SSL_LOG = '/Users/eab06/Desktop/WJB/PythonProjects/HT_Data/src/GetAllDoctors/DetermineTEDSpecialists/__TMP_SSL_KEY.log'


class Colors:  # TODO: use actual package
    OK = '\033[94m'
    YELLOW = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    UNDERLINE = '\033[4m'


class Timeout(Exception):
    MESSAGE = f"""\n{Colors.FAIL}Timed out.{Colors.ENDC} Retry last cycle {Colors.YELLOW}(y){Colors.ENDC} \
or continue with empty data {Colors.YELLOW}(n){Colors.ENDC}?
Latter option assumes current zipcode (`no_data_radius <= 0`) or surrounding \
`no_data_radius` miles {Colors.YELLOW}has zero doctors{Colors.ENDC}. """


class TepezzaInterface:
    URL = 'https://www.tepezza.com/ted-specialist-finder'
    CRS = "+proj=aeqd +lat_0=90 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84 +datum=WGS84 +units=m no_defs"  # ESRI:102016
    TSHARK_CMD = f'{TSHARK} -l -x -i en0 -o ssl.keylog_file:{TMP_CHROME_SSL_LOG} -Y json -T ek'
    RE_JSON_RAW = re.compile('"json_raw": "([a-f0-9]+)"')

    def __init__(self):
        self.__exit__()

    def startup(self) -> None:
        self._load_shps()
        self._load_chrome()

    def _load_shps(self) -> None:
        print("Reading zipcode shapefile...")  # TODO: use logging

        # shapefile from
        # https://www2.census.gov/geo/tiger/TIGER2019/ZCTA5/tl_2019_us_zcta510.zip,
        # converted to CRS
        self._shp = gpd.read_file(
            # 1000 random zipcodes for testing
            '/Users/eab06/Desktop/WJB/PythonProjects/HT_Data/src/GetAllDoctors/DetermineTEDSpecialists/shapefiles/zipcodes',
        )
        self._shp.set_index('ZCTA5CE10', inplace=True)

        print("Reading dissolved shapefile...")
        self._incomplete = gpd.read_file(
            '/Users/eab06/Desktop/WJB/PythonProjects/HT_Data/src/GetAllDoctors/DetermineTEDSpecialists/shapefiles/dissolved')
        self._incomplete.to_crs(crs=self.CRS, inplace=True)  # FIXME

        print("Reading representative multipoint shapefile...")
        self._repr_multipoint = gpd.read_file(
            '/Users/eab06/Desktop/WJB/PythonProjects/HT_Data/src/GetAllDoctors/DetermineTEDSpecialists/shapefiles/representative_pts_multipoint'
        ).geometry[0]



        # FIXME

    def _load_chrome(self) -> None:
        Path(TMP_CHROME_SSL_LOG).touch(exist_ok=True)
        os.environ['SSLKEYLOGFILE'] = TMP_CHROME_SSL_LOG

        self.chrome_subp = subprocess.Popen(
            [PATH_TO_CHROME,
             # https://ivanderevianko.com/2020/04/disable-logging-in-selenium-chromedriver
             '--output=/dev/null',
             '--log-level=3',
             '--disable-logging',
             f"--user-data-dir={TMP_CHROME_PROFILE}"],
            stdout=subprocess.DEVNULL)
        print(
            f"New Chrome profile launched; {Colors.FAIL}Do not proceed to Tepezza{Colors.ENDC}.")

        print(f"{Colors.OK}Initialization complete.\n{Colors.ENDC}")

        while True:
            in_ = input(
                f"Is Chrome loaded? Ready to begin? Press {Colors.YELLOW}\"y\"{Colors.ENDC} to continue, then {Colors.YELLOW}proceed to Tepezza{Colors.ENDC}.")
            if in_ == "y":
                break

        self.network_subp = subprocess.Popen(
            shlex.split(self.TSHARK_CMD), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1, universal_newlines=True)
        os.set_blocking(self.network_subp.stdout.fileno(), False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        try:
            self.chrome_subp.kill()
        except AttributeError:
            pass

        try:
            self.network_subp.kill()
        except AttributeError:
            pass

        if os.path.exists(TMP_CHROME_PROFILE):
            print('chrome profile exists')
            shutil.rmtree(TMP_CHROME_PROFILE)
        if os.path.exists(TMP_CHROME_SSL_LOG):
            os.remove(TMP_CHROME_SSL_LOG)

        print(f"{Colors.OK}Cleanup complete.{Colors.ENDC}")

    def _watch_network(self) -> 'JSON':
        """
        Get network data after zipcode data request is made.
        """

        while True:
            if input(
                f"Once zipcode has been searched, press {Colors.YELLOW}\"y\"{Colors.ENDC} to continue. "
            ) == 'y':
                break
        counter = 0
        s = b''
        while True:
            try:
                in_ = bytes(self.network_subp.stdout.readline(), 'utf-8')
                if in_ == b'':
                    continue
                counter += 1
                pkt = json.loads(in_)
                s = b''

            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:  # overflowed
                print(counter, str(e)[:min(1000, len(str(e)))], type(e))
                s += in_
                with open(f'error_{counter}.json', 'wb+') as f:
                    f.write(s)
                try:
                    pkt = json.loads(s)
                except (json.JSONDecodeError, json.decoder.JSONDecodeError):
                    continue
            s = b''
            try:
                match_obj = self.RE_JSON_RAW.search(json.dumps(pkt))
                assert match_obj, "match_obj"
                json_raw = match_obj.group(1)
                assert json_raw, "json_raw"

                d = json.loads(bytearray.fromhex(json_raw).decode('utf-8'))
                assert d['success'], 'success'

                for i in d['data']:
                    i.pop('GeoLocation')
                return d['data']
            except (KeyError, IndexError, AssertionError, TypeError) as e:
                with open(f'error_{counter}.json', 'wb+') as f:
                    f.write(in_)
                print(counter, str(e)[:min(1000, len(str(e)))], type(e))
            except AttributeError:
                print('empty')
                return []

    def get_data(self, 
        starting_zip: str, 
        radius_func: Callable[[Iterable[float]], float], 
        filepath: str, 
        timeout: int = 60, 
        no_data_radius: int = 0) -> pandas.DataFrame:
        
        df = pandas.DataFrame(
            columns=['Distance', 'VEEVA_ID', 'FIRST_NAME', 'LAST_NAME', 'MIDDLE_NAME', 'ADDRESS_LINE1',
                     'ADDRESS_LINE2', 'CITY', 'STATE', 'ZIP', 'PRIMARY_DEGREE', 'AMA_SPECIALITY', 'PHONE',
                     'MOBILE', 'EMAIL', 'Latitude', 'Longitude', 'Attributes', 'PhysicianAttributes'
                     ]
        )
        
        target_zip_name = starting_zip
        initial_area = self._incomplete.area.iloc[0]
        completed_zips = set()

        while not self._incomplete.is_empty.all():

            print(
                f"Look up zipcode {Colors.YELLOW}{Colors.UNDERLINE}{target_zip_name}{Colors.ENDC}.")
            clipboard.copy(target_zip_name)

            def handler(sig, frame): raise Timeout()
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(timeout)
            try:
                data = self._watch_network()
            except Timeout:
                data = []
                retry = None
                while True:
                    in_ = input(Timeout.MESSAGE)
                    print(in_)
                    if in_ == 'y':
                        retry = True
                        break
                    elif in_ == 'n':
                        retry = False
                        break
                print('\n')
                if retry:
                    # back to start of loop like nothing ever happened
                    continue

            if data is not []:
                df = df.append(data)
                df.to_csv(filepath)
            print(f"Data received and saved.")

            target_zip_obj = self._shp.at[target_zip_name, 'geometry']

            if data != [] or no_data_radius > 0:
                if data == []:
                    radius_mi = no_data_radius
                else:

                    radius_mi = radius_func( (i['Distance'] for i in data) )

                radius_meters = radius_mi * 5280 * 12 * 2.54 / 100
                complete = target_zip_obj.representative_point().buffer(radius_meters)
            else:
                complete = target_zip_obj
            complete_gdf = gpd.GeoDataFrame(
                {'geometry': complete}, index=[0], crs=self.CRS)

            print("Completed GeoDataFrame found.")

            self._incomplete = gpd.overlay(
                self._incomplete, complete_gdf, how='difference')

            self._incomplete.plot()
            # complete_gdf.plot()
            plt.show()

            print("Overlay operation complete.")

            pct_done = round(
                (1 - self._incomplete.area.iloc[0] / initial_area) * 100, 4)
            print(f"{Colors.YELLOW}{pct_done}{Colors.ENDC}% done by area.\n")

            target_point = self._incomplete.representative_point().iloc[0]
            nearest_repr_pt = nearest_points(
                target_point,
                self._repr_multipoint
            )[1]
            zipcode_index_idx = self._shp.sindex.query(nearest_repr_pt)[0]
            target_zip_name = self._shp.index[zipcode_index_idx]

            completed_zips.add(target_zip_name)

            print(
                f"New points generated; {Colors.OK}cycle complete{Colors.ENDC}.\n")

        return df


if __name__ == '__main__':
    def rfunc(radii: Iterable[float]) -> float:
        return sqrt(median(radii))

    try:
        with TepezzaInterface() as ti:
            ti.startup()
            ti.get_data('60609', rfunc, 'full_data.csv', 36000, 50)
    except KeyboardInterrupt:
        pass