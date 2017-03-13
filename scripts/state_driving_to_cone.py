#!/usr/bin/env python
#
# Copyright 2017 Robot Garden, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""read cone_finder location messages and publish OverrideRCIn messages"""
#
# Node publishes /mavros_msgs/OverrideRCIn messages
# using cone_finder/location messages
#

import sys, argparse, math

# ROS
import rospy
#from std_msgs.msg import String
#from ._WaypointPull import *
#
from uav_state import MODE as MAVMODE
import uav_state
import uav_control

import exec_comm
from exec_comm import MSG_TO_STATE
from exec_comm import MSG_TO_EXEC
from state_and_transition import STATE
from state_and_transition import TRANSITION

from mavros_msgs.msg import OverrideRCIn
from cone_finder.msg import location_msgs as Locations

# We will get angle between +pi/2 to -pi/2 for steering
# We will get 480 pixels range for throttle but should limit this
class Args(object):
    # Typically less than 1 unless the range isn't responsive
    throttle_factor = 1.0
    steering_factor = 1.0


# Globals

args = Args()

#settings = termios.tcgetattr(sys.stdin)
hard_limits = [1000, 1500, 2000]  # microseconds for servo signal
steering_limits = [1135, 1435, 1735]  # middle is neutral
# throttle_limits = [1200, 1500, 1800]  # middle is neutral
throttle_limits = [1650, 1650, 1800]  # fwd range only; for testing; middle is NOT neutral


#
#
#
#
# Exec command listener callback
#
def cmd_callback(data):
    """Exec command listener callback"""
    # Parses the message
    # State is returned. If message state is our state, cmd is updated.
    the_state = __ExecComm.parse_msg_to_state(data.data)

    if the_state == __ExecComm.state:
        rospy.loginfo(rospy.get_caller_id() + ' cmd_callback: %s', data.data)
        # Handle start, reset, pause, etc.
        if __ExecComm.cmd == MSG_TO_STATE.START.name:
            state_start()
        elif __ExecComm.cmd == MSG_TO_STATE.RESET.name:
            state_reset()
        elif __ExecComm.cmd == MSG_TO_STATE.PAUSE.name:
            state_pause()
        else:
            rospy.logwarn('Invalid cmd: '+data.data)




#
# Reset the state
# For safety, for now set to HOLD
#
def state_reset():
    """Reset the state"""
    # Set UAV mode to hold while we get this state started
    __UAV_State.set_mode(MAVMODE.HOLD.name)
    __UAV_State.set_arm(False)




#
# Pause the state
#
def state_pause():
    """Pause the state"""
    # Set UAV mode to hold while we get this state started
    __UAV_State.set_mode(MAVMODE.HOLD.name)
    __UAV_State.set_arm(False)




#
#
## Transitions
# - touched_cone => Driving_away_from_cone
# - passed_cone => Following_waypoint
# - segment_timeout => Following_waypoint
# - touched_last_cone => Success
# - passed_last_cone => Failure
# - course_timeout => Failure
#
def state_start():
    """Start the state"""
    state_name = STATE.Driving_toward_cone.name
    rospy.loginfo('state_start %s', state_name)

    # TODO Setting mode to HOLD is precautionary.
    # Set UAV mode to hold while we get this state started
    __UAV_State.set_mode(MAVMODE.HOLD.name)
    __UAV_State.set_arm(False)

    #
    touched_cone = False
    #
    passed_cone = True
    segment_timeout = False
    #
    touched_last_cone = True
    #
    passed_last_cone = False
    course_timeout = False

    # Get radio calibration values
    # [1] is neutral
    steering_limits = [__UAV_Control.get_param_int('RC1_MIN'),
                       __UAV_Control.get_param_int('RC1_TRIM'),
                       __UAV_Control.get_param_int('RC1_MAX')]

    throttle_limits = [__UAV_Control.get_param_int('RC3_MIN'),
                       __UAV_Control.get_param_int('RC3_TRIM'),
                       __UAV_Control.get_param_int('RC3_MAX')]

    rate = rospy.Rate(2) # 2 hz
    global touched
    touched = False
    touchSubscriber = rospy.Subscriber('/touch', Locations, touched_cb)

    global subscriber
    subscriber = rospy.Subscriber('/cone_finder/locations', Locations, seek_cone)
    __UAV_State.set_mode(MAVMODE.MANUAL.name)
    __UAV_State.set_arm(True)

    # Driving To cone loop
    segment_duration_sec = rospy.get_param("/SEGMENT_DURATION_SEC")
    timeout = rospy.Time.now() + rospy.Duration(segment_duration_sec)
    old_timeout_secs = 0

    while not rospy.is_shutdown():
        timeout_secs = int(timeout.__sub__(rospy.Time.now()).to_sec())
        if timeout_secs <> old_timeout_secs:
            rospy.loginfo(
                'In %s state NODE. Timeout in: %d',
                state_name,
                timeout_secs)
        old_timeout_secs = timeout_secs
        if touched:
            touched_cone = True # Signal we touched a cone
            break
        if __ExecComm.cmd != MSG_TO_STATE.START.name:
            # TODO What if any transition?
            rospy.loginfo('State aborted: %s with command %s', 
                          state_name, __ExecComm.cmd)
            break
        if rospy.Time.now() > timeout:
            segment_timeout = True
            # TODO What's the transition?
            rospy.loginfo('State timed out: %s', state_name)
            break
        rate.sleep()

    # Stop subscribing
    subscriber.unregister()
    touchSubscriber.unregister()
    __UAV_Control.set_throttle_servo(throttle_limits[1], steering_limits[1])

    # Put in safe mode
    __UAV_State.set_mode(MAVMODE.HOLD.name)
    __UAV_State.set_arm(False)

    # Publish transition
    if passed_last_cone:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.passed_last_cone.name)
    elif course_timeout:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.course_timeout.name)
    elif touched_last_cone:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.touched_last_cone.name)
    elif passed_cone:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.passed_cone.name)
    elif segment_timeout:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.segment_timeout.name)
    elif touched_cone:
        __ExecComm.send_message_to_exec(MSG_TO_EXEC.DONE.name, TRANSITION.touched_cone.name)



#
# Touch listener
#
def touched_cb(data):
    """Touch listener"""
    global touched
    rospy.loginfo("touched_cb: "+str(data.data))
    if data.data == True:
        touched = True




#
#
#
def seek_cone(loc):
    rospy.loginfo('seek_cone')
    # Sort the poses by y distance to get the nearest cone
    poses = sorted(loc.poses, key=lambda loc: loc.y)
    cone_loc = poses[0]

    steering = steering_limits[1]
    # Steer if not in front
    if (cone_loc.x < -20) or (cone_loc.x > 20):
        # Sin(theta)
        z = math.sqrt(cone_loc.x*cone_loc.x + cone_loc.y*cone_loc.y)
        #steering = steering + args.steering_factor*500*cone_loc.x/z
        steering = steering + args.steering_factor*2*cone_loc.x

    # Slowest approach to cone
    throttle = throttle_limits[1] + 20
    # Use real depth when available for throttle
    if cone_loc.z > 0:
        # Real depth is in mm and maximum would probably be less than 6m
        if cone_loc.z > 300:
            throttle = throttle + args.throttle_factor*(cone_loc.z - 300)/20
    else:
        y = cone_loc.y - 40
        if y > 0:
            throttle = throttle + args.throttle_factor*(y)

    #-- test with fixed throttle to start
    throttle = 1675

    # Everything must be bounded
    if steering > steering_limits[2]:
        steering = steering_limits[2]
    if steering < steering_limits[0]:
        steering = steering_limits[0]
    if throttle > throttle_limits[2]:
        throttle = throttle_limits[2]
    if throttle < throttle_limits[0]:
        throttle = throttle_limits[0]

    __UAV_Control.set_throttle_servo(throttle, steering)




#
# Start our node
#
def state_node(state_name):
    """Start node"""

    rospy.loginfo('State node starting: %s', state_name)
    rospy.init_node(state_name, anonymous=False)

    # Initialize UAV models
    global __UAV_State
    __UAV_State = uav_state.UAV_State()
    global __UAV_Control
    __UAV_Control = uav_control.UAV_Control()

    # Exec/state comm
    global __ExecComm
    __ExecComm = exec_comm.ExecComm(state_name, cmd_callback)

    rate = rospy.Rate(10) # 10 hz
    while not rospy.is_shutdown():
        rate.sleep()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Drive to cone found')
    parser.add_argument('--throttle_factor', '-t', default=1.0, type=float,
                        help='Throttle step size factor')
    parser.add_argument('--steering_factor', '-s', default=1.0, type=float,
                        help='Steering step size factor')
    parser.parse_args(rospy.myargv(sys.argv[1:]), args)
    try:
        state_node(STATE.Driving_toward_cone.name)
    except rospy.ROSInterruptException:
        pass

