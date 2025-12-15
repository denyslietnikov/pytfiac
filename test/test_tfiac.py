import unittest
from time import sleep

from pytfiac import (
    FAN_MODE,
    ON_MODE,
    OPERATION_MODE,
    SET_SWING,
    SWING_MODE,
    TARGET_TEMP,
    Tfiac,
)

HOST = "192.168.1.108"


OPERATION_LIST = ["heat", "selfFeel", "dehumi", "fan", "cool"]
FAN_LIST = ["Auto", "Low", "Middle", "High"]
FAN_LIST_LIMIT = ["Low", "Middle", "High"]

SWING_LIST = [
    "Off",
    "Vertical",
    "Horizontal",
    "Both",
]

MIN_TEMP = 73
MAX_TEMP = 88

SHORT_WAIT = 2
LONG_WAIT = 3


class TfiacTest(unittest.TestCase):
    def setUp(self):
        self.tfiac = Tfiac(HOST)

    def test_init(self):
        sleep(SHORT_WAIT)
        status = self.tfiac.status
        self.assertTrue(status.get(ON_MODE))
        print("Current status is {}".format(status[ON_MODE]))
        self.assertIn(status[ON_MODE], ["on", "off"])

    def switch(self, tfiac, on_off):
        tfiac.set_state(ON_MODE, on_off)
        sleep(SHORT_WAIT)
        tfiac.update()
        self.assertEqual(tfiac.status[ON_MODE], on_off)

    def test_power(self):
        self.switch(self.tfiac, "off")
        sleep(SHORT_WAIT)
        self.switch(self.tfiac, "on")
        sleep(SHORT_WAIT)
        self.switch(self.tfiac, "off")
        sleep(SHORT_WAIT)

    def change_operative_mode(self, tfiac, mode):
        tfiac.set_state(OPERATION_MODE, mode)
        sleep(SHORT_WAIT)
        tfiac.update()
        self.assertEqual(tfiac.status[OPERATION_MODE], mode)

    def test_operation_modes(self):
        self.switch(self.tfiac, "on")
        for mode in OPERATION_LIST:
            self.change_operative_mode(self.tfiac, mode)
            sleep(LONG_WAIT)
        self.switch(self.tfiac, "off")
        sleep(LONG_WAIT)

    def test_fan_modes(self):
        self.switch(self.tfiac, "on")
        for curr_mode in ["heat", "selfFeel", "cool"]:
            self.change_operative_mode(self.tfiac, curr_mode)
            for curr_fan in FAN_LIST:
                self.tfiac.set_state(FAN_MODE, curr_fan)
                sleep(SHORT_WAIT)
                self.tfiac.update()
                self.assertEqual(self.tfiac.status[FAN_MODE], curr_fan)
        self.change_operative_mode(self.tfiac, "fan")
        for curr_fan in FAN_LIST_LIMIT:
            self.tfiac.set_state(FAN_MODE, curr_fan)
            sleep(SHORT_WAIT)
            self.tfiac.update()
            self.assertEqual(self.tfiac.status[FAN_MODE], curr_fan)
        self.switch(self.tfiac, "off")

    def test_swing(self):
        self.switch(self.tfiac, "on")
        for curr_mode in OPERATION_LIST:
            self.change_operative_mode(self.tfiac, curr_mode)
            for curr_swing in SET_SWING:
                self.tfiac.set_swing(curr_swing)
                sleep(SHORT_WAIT)
                self.tfiac.update()
                self.assertEqual(self.tfiac.status[SWING_MODE], curr_swing)
        self.switch(self.tfiac, "off")

    def set_temp(self, tfiac, temperature):
        print("Setting temperature: {}.".format(temperature))
        tfiac.set_state(TARGET_TEMP, temperature)
        sleep(LONG_WAIT)
        tfiac.update()
        self.assertTrue(
            abs(tfiac.status[TARGET_TEMP] - temperature) <= 1.0,
            "Tried to set {} and got {}".format(temperature, tfiac.status[TARGET_TEMP]),
        )

    def test_temperatures(self):
        self.switch(self.tfiac, "on")
        for curr_mode in ["heat", "selfFeel", "cool"]:
            print("Currently testing {} mode.".format(curr_mode))
            self.change_operative_mode(self.tfiac, curr_mode)
            self.set_temp(self.tfiac, MIN_TEMP)
            sleep(LONG_WAIT)
            self.set_temp(self.tfiac, MAX_TEMP)
            sleep(LONG_WAIT)
            middle_point = int((MIN_TEMP + MAX_TEMP) / 2)
            self.set_temp(self.tfiac, middle_point)
            sleep(LONG_WAIT)
            for incr_temp in range(middle_point - 6, middle_point + 6, 2):
                self.set_temp(self.tfiac, incr_temp)
                sleep(SHORT_WAIT)
        self.switch(self.tfiac, "off")


if __name__ == "__main__":
    unittest.main()
