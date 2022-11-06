"""Support for Libre Hardware Monitor Sensor Platform."""
from __future__ import annotations

from datetime import timedelta
import logging

import requests
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle
from homeassistant.util.dt import utcnow

_LOGGER = logging.getLogger(__name__)

STATE_MIN_VALUE = "minimal_value"
STATE_MAX_VALUE = "maximum_value"
STATE_VALUE = "value"
STATE_OBJECT = "object"
CONF_INTERVAL = "interval"

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=15)
SCAN_INTERVAL = timedelta(seconds=30)
RETRY_INTERVAL = timedelta(seconds=30)

LHM_VALUE = "Value"
LHM_MIN = "Min"
LHM_MAX = "Max"
LHM_CHILDREN = "Children"
LHM_NAME = "Text"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_HOST): cv.string, vol.Optional(CONF_PORT, default=8085): cv.port, vol.Optional(CONF_USERNAME, default=''): cv.string, vol.Optional(CONF_PASSWORD, default=''): cv.string}
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Libre Hardware Monitor platform."""
    data = LibreHardwareMonitorData(config, hass)
    if data.data is None:
        raise PlatformNotReady
    add_entities(data.devices, True)


class LibreHardwareMonitorDevice(SensorEntity):
    """Device used to display information from LibreHardwareMonitor."""

    def __init__(self, data, name, path, unit_of_measurement):
        """Initialize an LibreHardwareMonitor sensor."""
        self._name = name
        self._data = data
        self.path = path
        self.attributes = {}
        self._unit_of_measurement = unit_of_measurement

        self.value = None

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def native_value(self):
        """Return the state of the device."""
        return self.value

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the entity."""
        return self.attributes

    @classmethod
    def parse_number(cls, string):
        """In some locales a decimal numbers uses ',' instead of '.'."""
        return string.replace(",", ".")

    def update(self) -> None:
        """Update the device from a new JSON object."""
        self._data.update()

        array = self._data.data[LHM_CHILDREN]
        _attributes = {}

        for path_index, path_number in enumerate(self.path):
            values = array[path_number]

            if path_index == len(self.path) - 1:
                self.value = self.parse_number(values[LHM_VALUE].split(" ")[0])
                _attributes.update(
                    {
                        "name": values[LHM_NAME],
                        STATE_MIN_VALUE: self.parse_number(
                            values[LHM_MIN].split(" ")[0]
                        ),
                        STATE_MAX_VALUE: self.parse_number(
                            values[LHM_MAX].split(" ")[0]
                        ),
                    }
                )

                self.attributes = _attributes
                return
            array = array[path_number][LHM_CHILDREN]
            _attributes.update({f"level_{path_index}": values[LHM_NAME]})


class LibreHardwareMonitorData:
    """Class used to pull data from LHM and create sensors."""

    def __init__(self, config, hass):
        """Initialize the Libre Hardware Monitor data-handler."""
        self.data = None
        self._config = config
        self._hass = hass
        self.devices = []
        self.initialize(utcnow())

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Hit by the timer with the configured interval."""
        if self.data is None:
            self.initialize(utcnow())
        else:
            self.refresh()

    def refresh(self):
        """Download and parse JSON from LHM."""
        data_url = (
            f"http://{self._config.get(CONF_HOST)}:"
            f"{self._config.get(CONF_PORT)}/data.json"
        )

        try:
            response = requests.get(data_url, auth=(self._config.get(CONF_USERNAME), self._config.get(CONF_PASSWORD)), timeout=30)
            if response.status_code == 401:
                _LOGGER.warning("Authentication rejected, are credentials correct?")
            elif response.status_code == 404:
                _LOGGER.debug("data.json was not found, are you connecting to LibreHardwareMonitor?")
            elif response.status_code != 200:
                _LOGGER.debug(f"Requesting data failed, response code: {response.status_code}")
            if response.status_code != 200:
                return            
            self.data = response.json()
        except requests.exceptions.ConnectionError:
            _LOGGER.debug("ConnectionError: Is LibreHardwareMonitor running?")
        except requests.exceptions.JSONDecodeError:
            _LOGGER.debug("JSONDecodeError: Is data correct?")

    def initialize(self, now):
        """Parse of the sensors and adding of devices."""
        self.refresh()

        if self.data is None:
            return

        self.devices = self.parse_children(self.data, [], [], [])

    def parse_children(self, json, devices, path, names):
        """Recursively loop through child objects, finding the values."""
        result = devices.copy()

        if json[LHM_CHILDREN]:
            for child_index in range(0, len(json[LHM_CHILDREN])):
                child_path = path.copy()
                child_path.append(child_index)

                child_names = names.copy()
                if path:
                    child_names.append(json[LHM_NAME])

                obj = json[LHM_CHILDREN][child_index]

                added_devices = self.parse_children(
                    obj, devices, child_path, child_names
                )

                result = result + added_devices
            return result

        if json[LHM_VALUE].find(" ") == -1:
            return result

        unit_of_measurement = json[LHM_VALUE].split(" ")[1]
        child_names = names.copy()
        child_names.append(json[LHM_NAME])
        fullname = " ".join(child_names)

        dev = LibreHardwareMonitorDevice(self, fullname, path, unit_of_measurement)

        result.append(dev)
        return result
