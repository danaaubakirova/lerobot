import time
from dataclasses import dataclass, field, replace

import torch
from stretch_body.gamepad_teleop import GamePadTeleop
from stretch_body.robot import Robot as StretchAPI

from lerobot.common.robot_devices.cameras.utils import Camera

# class LeRobotStretchTeleop(GamePadTeleop):
#     """Wrapper of stretch_body.gamepad_teleop.GamePadTeleop"""

#     def __init__(self):
#         super().__init__()


@dataclass
class StretchRobotConfig:
    robot_type: str | None = None
    cameras: dict[str, Camera] = field(default_factory=lambda: {})
    # TODO(aliberts): add comment
    max_relative_target: list[float] | float | None = None


class StretchRobot(StretchAPI):
    """Wrapper of stretch_body.robot.Robot"""

    robot_type = "stretch"

    def __init__(self, config: StretchRobotConfig | None = None, **kwargs):
        super().__init__()
        if config is None:
            config = StretchRobotConfig()
        # Overwrite config arguments using kwargs
        self.config = replace(config, **kwargs)

        self.cameras = self.config.cameras
        self.is_connected = False
        self.teleop = None
        self.logs = {}
        # TODO(aliberts): remove original low-level logging from stretch
        # RobotParams.set_logging_level("INFO")  # <-- not working

        self.state_keys = None

    def connect(self):
        self.is_connected = self.startup()
        # Connect the cameras
        for name in self.cameras:
            self.cameras[name].connect()
            self.is_connected = self.is_connected and self.cameras[name].is_connected

    def run_calibration(self):
        if not self.is_homed():
            self.home()

    def teleop_step(
        self, record_data=False
    ) -> None | tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        # TODO(aliberts): return proper types (ndarrays instead of torch.Tensors)
        if self.teleop is None:
            self.teleop = GamePadTeleop(robot_instance=False)
            self.teleop.startup(robot=self)

        before_read_t = time.perf_counter()
        self.teleop.do_motion(robot=self)
        state = self._get_state()
        action = self.teleop.gamepad_controller.get_state()
        self.logs["read_pos_dt_s"] = time.perf_counter() - before_read_t

        before_write_t = time.perf_counter()
        self.push_command()
        self.logs["write_pos_dt_s"] = time.perf_counter() - before_write_t

        if self.state_keys is None:
            self.state_keys = list(state)

        if not record_data:
            return

        state = torch.as_tensor(list(state.values()))
        action = torch.as_tensor(list(action.values()))

        # Capture images from cameras
        images = {}
        for name in self.cameras:
            before_camread_t = time.perf_counter()
            images[name] = self.cameras[name].async_read()
            images[name] = torch.from_numpy(images[name])
            self.logs[f"read_camera_{name}_dt_s"] = self.cameras[name].logs["delta_timestamp_s"]
            self.logs[f"async_read_camera_{name}_dt_s"] = time.perf_counter() - before_camread_t

        # Populate output dictionnaries
        obs_dict, action_dict = {}, {}
        obs_dict["observation.state"] = state
        action_dict["action"] = action
        for name in self.cameras:
            obs_dict[f"observation.images.{name}"] = images[name]

        return obs_dict, action_dict

    def _get_state(self) -> dict:
        status = self.get_status()
        return {
            "head_pan.pos": status["head"]["head_pan"]["pos"],
            "head_tilt.pos": status["head"]["head_tilt"]["pos"],
            "lift.pos": status["lift"]["pos"],
            "arm.pos": status["arm"]["pos"],
            "wrist_pitch.pos": status["end_of_arm"]["wrist_pitch"]["pos"],
            "wrist_roll.pos": status["end_of_arm"]["wrist_roll"]["pos"],
            "wrist_yaw.pos": status["end_of_arm"]["wrist_yaw"]["pos"],
            "base_x.vel": status["base"]["x_vel"],
            "base_y.vel": status["base"]["y_vel"],
            "base_theta.vel": status["base"]["theta_vel"],
        }

    def capture_observation(self): ...

    def send_action(self, action): ...

    def print_logs(self):
        ...
        # TODO(aliberts): move robot-specific logs logic here

    def disconnect(self):
        self.stop()
        if self.teleop is not None:
            self.teleop.gamepad_controller.stop()
            self.teleop.stop()

        if len(self.cameras) > 0:
            for cam in self.cameras.values():
                cam.disconnect()

        self.is_connected = False

    def __del__(self):
        self.disconnect()
