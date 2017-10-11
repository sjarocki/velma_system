#!/usr/bin/env python

# Copyright (c) 2014, Robot Control and Pattern Recognition Group, Warsaw University of Technology
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Warsaw University of Technology nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYright HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import rospy
import tf
import math
import time
import copy
from threading import Lock
from functools import partial

from geometry_msgs.msg import Wrench, Vector3, Twist
from sensor_msgs.msg import JointState
from barrett_hand_msgs.msg import *
from barrett_hand_action_msgs.msg import *
from cartesian_trajectory_msgs.msg import *
from motor_action_msgs.msg import *
from behavior_switch_action_msgs.msg import *
import actionlib
from trajectory_msgs.msg import *
from control_msgs.msg import FollowJointTrajectoryAction, FollowJointTrajectoryGoal, JointTolerance
import tf_conversions.posemath as pm

import PyKDL

import xml.dom.minidom as minidom

# reference frames:
# WO - world
# B - robot's base
# W - wrist
# Wr - right wrist
# Wl - left wrist
# G - grasp
# Gr - right grasp
# Gl - left grasp
# T - tool
# Tr - right tool
# Tl - left tool
# Frij - right hand finger i knuckle j

def isConfigurationClose(q_map, js, tolerance=0.1):
    for joint_name in q_map:
        if not joint_name in js[1]:
            return False
        if abs(q_map[joint_name] - js[1][joint_name]) > tolerance:
            return False
    return True

def isHeadConfigurationClose(current_q, dest_q, tolerance=0.1):
    return abs(current_q[0]-dest_q[0]) < tolerance and\
        abs(current_q[1]-dest_q[1]) < tolerance

def isHandConfigurationClose(current_q, dest_q, tolerance=0.1):
    return abs(current_q[0]-dest_q[3]) < tolerance and\
        abs(current_q[1]-dest_q[0]) < tolerance and\
        abs(current_q[4]-dest_q[1]) < tolerance and\
        abs(current_q[6]-dest_q[2]) < tolerance

class VelmaInterface:
    """
Class used as Velma robot Interface.
"""

    class VisualMesh:
        def __init__(self):
            self.type = "mesh"
            self.origin = None
            self.filename = None

    class Link:
        def __init__(self):
            self.name = None
            self.visuals = []

    def getLastJointState(self):
        result = None
        with self.joint_states_lock:
            if self.js_pos_history_idx >= 0:
                return copy.copy(self.js_pos_history[self.js_pos_history_idx])

    def getJointStateAtTime(self, time):
        with self.joint_states_lock:
            if self.js_pos_history_idx < 0:
                return None
                
            hist_len = len(self.js_pos_history)
            for step in range(hist_len-1):
                h1_idx = (self.js_pos_history_idx - step - 1) % hist_len
                h2_idx = (self.js_pos_history_idx - step) % hist_len
                if self.js_pos_history[h1_idx] == None or self.js_pos_history[h2_idx] == None:
                    return None

                time1 = self.js_pos_history[h1_idx][0]
                time2 = self.js_pos_history[h2_idx][0]
                if time > time1 and time <= time2:
                    factor = (time - time1).to_sec() / (time2 - time1).to_sec()
                    js_pos = {}
                    for joint_name in self.js_pos_history[h1_idx][1]:
                        js_pos[joint_name] = self.js_pos_history[h1_idx][1][joint_name] * (1.0 - factor) + self.js_pos_history[h2_idx][1][joint_name] * factor
                    return js_pos
            return None

    def jointStatesCallback(self, data):
        with self.joint_states_lock:
            joint_idx = 0
            for joint_name in data.name:
                self.js_pos[joint_name] = data.position[joint_idx]
                joint_idx += 1

            self.js_pos_history_idx = (self.js_pos_history_idx + 1) % len(self.js_pos_history)
            self.js_pos_history[self.js_pos_history_idx] = (data.header.stamp, copy.copy(self.js_pos))

    def getHandCurrentConfiguration(self, prefix):
        js = self.getLastJointState()
        q = [ js[1][prefix+'_HandFingerOneKnuckleOneJoint'],
            js[1][prefix+'_HandFingerOneKnuckleTwoJoint'],
            js[1][prefix+'_HandFingerOneKnuckleThreeJoint'],
            js[1][prefix+'_HandFingerTwoKnuckleOneJoint'],
            js[1][prefix+'_HandFingerTwoKnuckleTwoJoint'],
            js[1][prefix+'_HandFingerTwoKnuckleThreeJoint'],
            js[1][prefix+'_HandFingerThreeKnuckleTwoJoint'],
            js[1][prefix+'_HandFingerThreeKnuckleThreeJoint'] ]
        return q

    def getHandLeftCurrentConfiguration(self):
        return self.getHandCurrentConfiguration("left")

    def getHandRightCurrentConfiguration(self):
        return self.getHandCurrentConfiguration("right")

    def getHeadCurrentConfiguration(self):
        js = self.getLastJointState()
        q = [ js[1]["head_pan_joint"],
            js[1]["head_tilt_joint"] ]
        return q

    def allActionsConnected(self):
        allConnected = True
        for side in self.action_move_hand_client_connected:
            if not self.action_move_hand_client_connected[side]:
                self.action_move_hand_client_connected[side] = self.action_move_hand_client[side].wait_for_server(rospy.Duration.from_sec(0.001))
            if not self.action_cart_traj_client_connected[side]:
                self.action_cart_traj_client_connected[side] = self.action_cart_traj_client[side].wait_for_server(rospy.Duration.from_sec(0.001))
            allConnected = allConnected and self.action_move_hand_client_connected[side] and\
                self.action_cart_traj_client_connected[side]
        if not self.action_joint_traj_client_connected:
            self.action_joint_traj_client_connected = self.action_joint_traj_client.wait_for_server(rospy.Duration.from_sec(0.001))
        allConnected = allConnected and self.action_joint_traj_client_connected

        if not self.action_head_joint_traj_client_connected:
            self.action_head_joint_traj_client_connected = self.action_head_joint_traj_client.wait_for_server(rospy.Duration.from_sec(0.001))
        allConnected = allConnected and self.action_head_joint_traj_client_connected

        if not self.action_safe_col_client_connected:
            self.action_safe_col_client_connected = self.action_safe_col_client.wait_for_server(rospy.Duration.from_sec(0.001))
        allConnected = allConnected and self.action_safe_col_client_connected

        for motor in self.action_motor_client_connected:
            if not self.action_motor_client_connected[motor]:
                self.action_motor_client_connected[motor] = self.action_motor_client[motor].wait_for_server(rospy.Duration.from_sec(0.001))
            allConnected = allConnected and self.action_motor_client_connected[motor]

        return allConnected

    def waitForInit(self, timeout_s = None):
        time_start = time.time()
        can_break = False
        while not rospy.is_shutdown():
            with self.joint_states_lock:
                if self.js_pos_history_idx >= 0:
                    can_break = True
            if can_break and self.allActionsConnected():
                break
            rospy.sleep(0.1)
            time_now = time.time()
            if timeout_s and (time_now-time_start) > timeout_s:
                print "ERROR: waitForInit: ", can_break, self.action_move_hand_client_connected["right"],\
                    self.action_move_hand_client_connected["left"], self.action_cart_traj_client_connected["right"],\
                    self.action_cart_traj_client_connected["left"], self.action_joint_traj_client_connected,\
                    self.action_head_joint_traj_client_connected, self.action_motor_client_connected,\
                    self.action_safe_col_client_connected
                return False
        return True

    def getBodyJointLowerLimits(self):
        return self.body_joint_lower_limits

    def getBodyJointUpperLimits(self):
        return self.body_joint_upper_limits

    def getHeadJointLowerLimits(self):
        return self.head_joint_lower_limits

    def getHeadJointUpperLimits(self):
        return self.head_joint_upper_limits

    def __init__(self, namespace="/velma_controller"):

        self.listener = tf.TransformListener();

        # read the joint information from the ROS parameter server
        self.body_joint_names = rospy.get_param(namespace+"/JntImpAction/joint_names")
        self.body_joint_lower_limits = rospy.get_param(namespace+"/JntImpAction/lower_limits")
        self.body_joint_upper_limits = rospy.get_param(namespace+"/JntImpAction/upper_limits")

#        self.head_joint_names = rospy.get_param(namespace+"/HeadSplineTrajectoryActionJoint/joint_names")
#        self.head_joint_lower_limits = rospy.get_param(namespace+"/HeadSplineTrajectoryActionJoint/lower_limits")
#        self.head_joint_upper_limits = rospy.get_param(namespace+"/HeadSplineTrajectoryActionJoint/upper_limits")

        self.all_joint_names = rospy.get_param(namespace+"/JntPub/joint_names")

        self.all_links = []

        robot_description_xml = rospy.get_param("/robot_description")
        #print robot_description_xml

        dom = minidom.parseString(robot_description_xml)
        robot = dom.getElementsByTagName("robot")
        if len(robot) != 1:
            raise Exception("Could not parse robot_description xml: wrong number of 'robot' elements.")
        links = robot[0].getElementsByTagName("link")
        for l in links:
            name = l.getAttribute("name")
            if name == None:
                raise Exception("Could not parse robot_description xml: link element has no name.")

            obj_link = self.Link()
            obj_link.name = name

            visual = l.getElementsByTagName("visual")
            for v in visual:
                origin = v.getElementsByTagName("origin")
                if len(origin) != 1:
                    raise Exception("Could not parse robot_description xml: wrong number of origin elements in link " + name)
                rpy = origin[0].getAttribute("rpy").split()
                xyz = origin[0].getAttribute("xyz").split()
                frame = PyKDL.Frame(PyKDL.Rotation.RPY(float(rpy[0]), float(rpy[1]), float(rpy[2])), PyKDL.Vector(float(xyz[0]), float(xyz[1]), float(xyz[2])))
                geometry = v.getElementsByTagName("geometry")
                if len(geometry) != 1:
                    raise Exception("Could not parse robot_description xml: wrong number of geometry elements in link " + name)
                mesh = geometry[0].getElementsByTagName("mesh")
                if len(mesh) == 1:
                    obj_visual = self.VisualMesh()
                    obj_visual.filename = mesh[0].getAttribute("filename")
                    #print "link:", name, " mesh:", obj_visual.filename
                    obj_visual.origin = frame
                    obj_link.visuals.append( obj_visual )
                else:
                    pass
                    #print "link:", name, " other visual"

            self.all_links.append(obj_link)

        #print "self.all_links", self.all_links
        #print "self.all_joint_names", self.all_joint_names

        self.js_pos = {}
        self.js_pos_history = []
        for i in range(200):
            self.js_pos_history.append( None )
        self.js_pos_history_idx = -1

        self.T_B_L = [PyKDL.Frame(),PyKDL.Frame(),PyKDL.Frame(),PyKDL.Frame(),PyKDL.Frame(),PyKDL.Frame(),PyKDL.Frame()]

        # parameters
        self.k_error = Wrench(Vector3(1.0, 1.0, 1.0), Vector3(0.5, 0.5, 0.5))
        self.T_B_W = None
        self.T_W_T = None
        self.T_Wl_El = None
        self.T_Wr_Er = None
        self.T_El_Wl = None
        self.T_Er_Wr = None
        self.current_max_wrench = Wrench(Vector3(20, 20, 20), Vector3(20, 20, 20))
        self.wrench_emergency_stop = False
        self.exit_on_emergency_stop = True

        self.last_contact_time = rospy.Time.now()

        self.joint_states_lock = Lock()

        # for score function
        self.failure_reason = "unknown"

        self.emergency_stop_active = False

        self.action_move_hand_client_connected = {'right':False, 'left':False}
        self.action_cart_traj_client_connected = {'right':False, 'left':False}
        self.action_joint_traj_client_connected = False
        self.action_head_joint_traj_client_connected = False
        self.action_safe_col_client_connected = False

        self.action_motor_client_connected = {'hp':False, 'ht':False, 't':False}

        # cartesian wrist trajectory for right arm
        self.action_cart_traj_client = {
            'right':actionlib.SimpleActionClient("/right_arm/cartesian_trajectory", CartImpAction),
            'left':actionlib.SimpleActionClient("/left_arm/cartesian_trajectory", CartImpAction)
            }

        # joint trajectory for arms and torso
        self.action_joint_traj_client = actionlib.SimpleActionClient("/spline_trajectory_action_joint", FollowJointTrajectoryAction)

        # joint trajectory for head
        self.action_head_joint_traj_client = actionlib.SimpleActionClient("/head_spline_trajectory_action_joint", FollowJointTrajectoryAction)

        self.action_safe_col_client = actionlib.SimpleActionClient("/safe_col_action", BehaviorSwitchAction)

        # motor actions for head
        self.action_motor_client = {
            'hp':actionlib.SimpleActionClient("/motors/hp", MotorAction),
            'ht':actionlib.SimpleActionClient("/motors/ht", MotorAction),
            't':actionlib.SimpleActionClient("/motors/t", MotorAction)
            }

        # cartesian tool trajectory for arms in the wrist frames
#        self.action_tool_client = {
#            'right':actionlib.SimpleActionClient("/right_arm/tool_trajectory", CartesianTrajectoryAction),
#            'left':actionlib.SimpleActionClient("/left_arm/tool_trajectory", CartesianTrajectoryAction) }
#        self.action_tool_client["right"].wait_for_server()
#        self.action_tool_client["left"].wait_for_server()

        # cartesian impedance trajectory for right arm
#        self.action_impedance_client = {
#            'right':actionlib.SimpleActionClient("/right_arm/cartesian_impedance", CartesianImpedanceAction),
#            'left':actionlib.SimpleActionClient("/left_arm/cartesian_impedance", CartesianImpedanceAction) }
#        self.action_impedance_client["right"].wait_for_server()
#        self.action_impedance_client["left"].wait_for_server()

        self.action_move_hand_client = {
            'right':actionlib.SimpleActionClient("/right_hand/move_hand", BHMoveAction),
            'left':actionlib.SimpleActionClient("/left_hand/move_hand", BHMoveAction) }
#        self.action_move_hand_client["right"].wait_for_server()    # this check is done in waitForInit
#        self.action_move_hand_client["left"].wait_for_server()     # this check is done in waitForInit

#        self.pub_reset_left = rospy.Publisher("/left_hand/reset_fingers", std_msgs.msg.Empty, queue_size=100)
#        self.pub_reset_right = rospy.Publisher("/right_hand/reset_fingers", std_msgs.msg.Empty, queue_size=100)

        self.br = tf.TransformBroadcaster()

        rospy.sleep(1.0)
        
        self.wrench_tab = []
        self.wrench_tab_index = 0
        self.wrench_tab_len = 4000
        for i in range(0,self.wrench_tab_len):
            self.wrench_tab.append( Wrench(Vector3(), Vector3()) )

        self.listener_joint_states = rospy.Subscriber('/joint_states', JointState, self.jointStatesCallback)

        subscribed_topics_list = [
            ('/right_arm/transformed_wrench', geometry_msgs.msg.Wrench),
            ('/left_arm/transformed_wrench', geometry_msgs.msg.Wrench),
            ('/right_arm/wrench', geometry_msgs.msg.Wrench),
            ('/left_arm/wrench', geometry_msgs.msg.Wrench),
            ('/right_arm/ft_sensor/wrench', geometry_msgs.msg.Wrench),
            ('/left_arm/ft_sensor/wrench', geometry_msgs.msg.Wrench), ]

        self.subscribed_topics = {}

        for topic in subscribed_topics_list:
            self.subscribed_topics[topic[0]] = [Lock(), None]
            sub = rospy.Subscriber(topic[0], topic[1], partial( self.topicCallback, topic = topic[0] ))
            self.subscribed_topics[topic[0]].append(sub)

    def topicCallback(self, data, topic):
        self.subscribed_topics[topic][0].acquire()
        self.subscribed_topics[topic][1] = copy.copy(data)
        self.subscribed_topics[topic][0].release()

    def getTopicData(self, topic):
        self.subscribed_topics[topic][0].acquire()
        data = copy.copy(self.subscribed_topics[topic][1])
        self.subscribed_topics[topic][0].release()
        return data

    def getRawFTr(self):
        return self.wrenchROStoKDL( self.getTopicData('/right_arm/ft_sensor/wrench') )

    def getRawFTl(self):
        return self.wrenchROStoKDL( self.getTopicData('/left_arm/ft_sensor/wrench') )

    def getTransformedFTr(self):
        return self.wrenchROStoKDL( self.getTopicData('/right_arm/transformed_wrench') )

    def getTransformedFTl(self):
        return self.wrenchROStoKDL( self.getTopicData('/left_arm/transformed_wrench') )

    def getWristWrenchr(self):
        return self.wrenchROStoKDL( self.getTopicData('/right_arm/wrench') )

    def getWristWrenchl(self):
        return self.wrenchROStoKDL( self.getTopicData('/left_arm/wrench') )

    def action_right_cart_traj_feedback_cb(self, feedback):
        self.action_right_cart_traj_feedback = copy.deepcopy(feedback)

    def wrenchKDLtoROS(self, wrKDL):
        return geometry_msgs.msg.Wrench(Vector3( wrKDL.force.x(), wrKDL.force.y(), wrKDL.force.z() ), Vector3( wrKDL.torque.x(), wrKDL.torque.y(), wrKDL.torque.z() ))

    def wrenchROStoKDL(self, wrROS):
        return PyKDL.Wrench( PyKDL.Vector(wrROS.force.x, wrROS.force.y, wrROS.force.z), PyKDL.Vector(wrROS.torque.x, wrROS.torque.y, wrROS.torque.z) )

    def switchToSafeColBehavior(self):
        goal = BehaviorSwitchGoal()
        goal.command = 1
        self.action_safe_col_client.send_goal(goal)

    def enableMotor(self, motor):
        goal = MotorGoal()
        goal.action = MotorGoal.ACTION_ENABLE
        self.action_motor_client[motor].send_goal(goal)

    def startHomingMotor(self, motor):
        goal = MotorGoal()
        goal.action = MotorGoal.ACTION_START_HOMING
        self.action_motor_client[motor].send_goal(goal)

    def waitForMotor(self, motor, timeout_s=0):
        if not self.action_motor_client[motor].wait_for_result(timeout=rospy.Duration(timeout_s)):
            return None
        result = self.action_motor_client[motor].get_result()

        error_code = result.error_code

        if error_code == MotorResult.ERROR_ALREADY_ENABLED:
            print "waitForMotor('" + motor + "'): ERROR_ALREADY_ENABLED (no error)"
            error_code = 0
        elif error_code == MotorResult.ERROR_HOMING_DONE:
            print "waitForMotor('" + motor + "'): ERROR_HOMING_DONE (no error)"
            error_code = 0
        if error_code != 0:
            print "waitForMotor('" + motor + "'): action failed with error_code=" + str(result.error_code)
        return error_code

    def enableHP(self):
        self.enableMotor("hp")

    def enableHT(self):
        self.enableMotor("ht")

    def enableT(self):
        self.enableMotor("t")

    def enableMotors(self, timeout=0):
        self.enableHP()
        self.enableHT()
        self.enableT()
        r_hp = self.waitForHP(timeout_s=timeout)
        r_ht = self.waitForHT(timeout_s=timeout)
        r_t = self.waitForT(timeout_s=timeout)
        if r_hp != 0:
            return r_hp
        if r_ht != 0:
            return r_ht
        return r_t

    def startHomingHP(self):
        self.startHomingMotor("hp")

    def startHomingHT(self):
        self.startHomingMotor("ht")

    def startHomingT(self):
        print "startHomingT: ERROR: torso motor should not be homed"

    def waitForHP(self, timeout_s=0):
        return self.waitForMotor("hp", timeout_s=timeout_s)

    def waitForHT(self, timeout_s=0):
        return self.waitForMotor("ht", timeout_s=timeout_s)

    def waitForT(self, timeout_s=0):
        return self.waitForMotor("t", timeout_s=timeout_s)

    def moveEffector(self, prefix, T_B_Td, t, max_wrench, start_time=0.01, stamp=None, path_tol=None):
        wrist_pose = pm.toMsg(T_B_Td)
#        self.br.sendTransform([wrist_pose.position.x, wrist_pose.position.y, wrist_pose.position.z], [wrist_pose.orientation.x, wrist_pose.orientation.y, wrist_pose.orientation.z, wrist_pose.orientation.w], rospy.Time.now(), "dest", "torso_base")

        action_trajectory_goal = CartImpGoal()
        if stamp != None:
            action_trajectory_goal.pose_trj.header.stamp = stamp
        else:
            action_trajectory_goal.pose_trj.header.stamp = rospy.Time.now() + rospy.Duration(start_time)
        action_trajectory_goal.pose_trj.points.append(CartesianTrajectoryPoint(
        rospy.Duration(t),
        wrist_pose,
        Twist()))
        action_trajectory_goal.wrench_constraint = self.wrenchKDLtoROS(max_wrench)
        if path_tol != None:
            action_trajectory_goal.path_tolerance.position = geometry_msgs.msg.Vector3( path_tol.vel.x(), path_tol.vel.y(), path_tol.vel.z() )
            action_trajectory_goal.path_tolerance.rotation = geometry_msgs.msg.Vector3( path_tol.rot.x(), path_tol.rot.y(), path_tol.rot.z() )
            action_trajectory_goal.goal_tolerance.position = geometry_msgs.msg.Vector3( path_tol.vel.x(), path_tol.vel.y(), path_tol.vel.z() )
            action_trajectory_goal.goal_tolerance.rotation = geometry_msgs.msg.Vector3( path_tol.rot.x(), path_tol.rot.y(), path_tol.rot.z() )
        self.current_max_wrench = self.wrenchKDLtoROS(max_wrench)
        self.action_cart_traj_client[prefix].send_goal(action_trajectory_goal, feedback_cb = self.action_right_cart_traj_feedback_cb)
        return True

    def moveEffectorLeft(self, T_B_Tld, t, max_wrench, start_time=0.01, stamp=None, path_tol=None):
        return self.moveEffector("left", T_B_Tld, t, max_wrench, start_time=start_time, stamp=stamp, path_tol=path_tol)

    def moveEffectorRight(self, T_B_Trd, t, max_wrench, start_time=0.01, stamp=None, path_tol=None):
        return self.moveEffector("right", T_B_Trd, t, max_wrench, start_time=start_time, stamp=stamp, path_tol=path_tol)

    def moveEffectorTraj(self, prefix, list_T_B_Td, times, max_wrench, start_time=0.01, stamp=None):

        action_trajectory_goal = CartImpGoal()
        if stamp != None:
            action_trajectory_goal.pose_trj.header.stamp = stamp
        else:
            action_trajectory_goal.pose_trj.header.stamp = rospy.Time.now() + rospy.Duration(start_time)

        i = 0
        for T_B_Td in list_T_B_Td:
            wrist_pose = pm.toMsg(T_B_Td)
            action_trajectory_goal.pose_trj.points.append(CartesianTrajectoryPoint(
            rospy.Duration(times[i]),
            wrist_pose,
            Twist()))
            i += 1

        action_trajectory_goal.wrench_constraint = self.wrenchKDLtoROS(max_wrench)
        self.current_max_wrench = max_wrench
        self.action_cart_traj_client[prefix].send_goal(action_trajectory_goal)

    def moveEffectorTrajLeft(self, prefix, list_T_B_Tld, times, max_wrench, start_time=0.01, stamp=None):
        self.moveEffectorTraj("left", list_T_B_Tld, times, max_wrench, start_time=start_time, stamp=stamp)

    def moveEffectorTrajRight(self, prefix, list_T_B_Trd, times, max_wrench, start_time=0.01, stamp=None):
        self.moveEffectorTraj("right", list_T_B_Trd, times, max_wrench, start_time=start_time, stamp=stamp)

    def moveCartImp(self, prefix, pose_list_T_B_Td, pose_times, tool_list_T_W_T, tool_times, imp_list, imp_times, max_wrench, start_time=0.01, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
        action_trajectory_goal = CartImpGoal()
        if stamp != None:
            action_trajectory_goal.pose_trj.header.stamp = stamp
        else:
            action_trajectory_goal.pose_trj.header.stamp = rospy.Time.now() + rospy.Duration(start_time)

        if pose_list_T_B_Td:
            i = 0
            for T_B_Td in pose_list_T_B_Td:
                wrist_pose = pm.toMsg(T_B_Td)
                action_trajectory_goal.pose_trj.points.append(CartesianTrajectoryPoint(
                rospy.Duration(pose_times[i]),
                wrist_pose,
                Twist()))
                i += 1

        if tool_list_T_W_T:
            i = 0
            for T_W_T in tool_list_T_W_T:
                tool_pose = pm.toMsg(T_W_T)
                action_trajectory_goal.tool_trj.points.append(CartesianTrajectoryPoint(
                rospy.Duration(tool_times[i]),
                tool_pose,
                Twist()))
                i += 1

        if imp_list:
            i = 0
            for k in imp_list:
                action_trajectory_goal.imp_trj.points.append(CartesianImpedanceTrajectoryPoint(
                rospy.Duration(imp_times[i]),
                CartesianImpedance(self.wrenchKDLtoROS(k), damping)))
                i += 1

        action_trajectory_goal.wrench_constraint = self.wrenchKDLtoROS(max_wrench)
        self.current_max_wrench = max_wrench
        self.action_cart_traj_client[prefix].send_goal(action_trajectory_goal)

        return True

    def moveCartImpRight(self, pose_list_T_B_Td, pose_times, tool_list_T_W_T, tool_times, imp_list, imp_times, max_wrench, start_time=0.01, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
        return self.moveCartImp("right", pose_list_T_B_Td, pose_times, tool_list_T_W_T, tool_times, imp_list, imp_times, max_wrench, start_time=start_time, stamp=stamp, damping=damping)

    def moveCartImpLeft(self, pose_list_T_B_Td, pose_times, tool_list_T_W_T, tool_times, imp_list, imp_times, max_wrench, start_time=0.01, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
        return self.moveCartImp("left", pose_list_T_B_Td, pose_times, tool_list_T_W_T, tool_times, imp_list, imp_times, max_wrench, start_time=start_time, stamp=stamp, damping=damping)

    cartesian_trajectory_result_names = {
        CartImpResult.SUCCESSFUL:'SUCCESSFUL',
        CartImpResult.INVALID_GOAL:'INVALID_GOAL',
        CartImpResult.OLD_HEADER_TIMESTAMP:'OLD_HEADER_TIMESTAMP',
        CartImpResult.PATH_TOLERANCE_VIOLATED:'PATH_TOLERANCE_VIOLATED',
        CartImpResult.GOAL_TOLERANCE_VIOLATED:'GOAL_TOLERANCE_VIOLATED',
        CartImpResult.UNKNOWN_ERROR:'UNKNOWN_ERROR', }

    def waitForEffector(self, prefix, timeout_s=None):
        if timeout_s == None:
            timeout_s = 0
        if not self.action_cart_traj_client[prefix].wait_for_result(timeout=rospy.Duration(timeout_s)):
            return None
        result = self.action_cart_traj_client[prefix].get_result()
        if result.error_code != 0:
            print "waitForEffector(" + prefix + "): action failed with error_code=" + str(result.error_code) + " (" + self.cartesian_trajectory_result_names[result.error_code] + ")"
        return result.error_code

    def waitForEffectorLeft(self, timeout_s=None):
        return self.waitForEffector("left", timeout_s=timeout_s)

    def waitForEffectorRight(self, timeout_s=None):
        return self.waitForEffector("right", timeout_s=timeout_s)

#    def isActiveEffector(self, prefix):
#        self.action_cart_traj_client[prefix].get_state()
#        result = self.action_cart_traj_client[prefix].get_result()
#        if result.error_code != 0:
#            print "waitForEffector(" + prefix + "): action failed with error_code=" + str(result.error_code) + " (" + self.cartesian_trajectory_result_names[result.error_code] + ")"
#        return result.error_code

#    def isActiveEffectorRight(self):
#        return self.isActiveEffector("right")

#    def isActiveEffectorLeft(self):
#        return self.isActiveEffector("left")

#TODO: remove moveTool methods
#    def moveTool(self, prefix, T_W_T, t, stamp=None):
#        ros_T_W_T = pm.toMsg(T_W_T)

#        action_tool_goal = CartImpGoal()
#        if stamp != None:
#            action_tool_goal.tool_trj.header.stamp = stamp
#        else:
#            action_tool_goal.tool_trj.header.stamp = rospy.Time.now() + rospy.Duration(0.01)
#        action_tool_goal.tool_trj.points.append(CartesianTrajectoryPoint(
#        rospy.Duration(t),
#        ros_T_W_T,
#        Twist()))
#        self.action_tool_client[prefix].send_goal(action_tool_goal)

#    def moveToolLeft(self, T_Wl_Tl, t, stamp=None):
#        self.moveTool("left", T_Wl_Tl, t, stamp=stamp)

#    def moveToolRight(self, T_Wr_Tr, t, stamp=None):
#        self.moveTool("right", T_Wr_Tr, t, stamp=stamp)

#    def waitForTool(self, prefix):
#        self.action_tool_client[prefix].wait_for_result()
#        result = self.action_tool_client[prefix].get_result()
#        if result.error_code != 0:
#            print "waitForTool(" + prefix + "): action failed with error_code=" + str(result.error_code)
#        return result.error_code

#    def waitForToolLeft(self):
#        return self.waitForTool("left")

#    def waitForToolRight(self):
#        return self.waitForTool("right")

#TODO: remove moveImpedance methods
#    def moveImpedance(self, prefix, k, t, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        action_impedance_goal = CartesianImpedanceGoal()
#        if stamp != None:
#            action_impedance_goal.imp_trj.header.stamp = stamp
#        else:
#            action_impedance_goal.imp_trj.header.stamp = rospy.Time.now() + rospy.Duration(0.2)
#        action_impedance_goal.imp_trj.points.append(CartesianImpedanceTrajectoryPoint(
#        rospy.Duration(t),
#        CartesianImpedance(self.wrenchKDLtoROS(k), damping)))
#        self.action_impedance_client[prefix].send_goal(action_impedance_goal)

#    def moveImpedanceLeft(self, k, t, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        self.moveImpedance("left", k, t, stamp=stamp, damping=damping)

#    def moveImpedanceRight(self, k, t, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        self.moveImpedance("right", k, t, stamp=stamp, damping=damping)

#    def moveImpedanceTraj(self, prefix, k_n, t_n, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        action_impedance_goal = CartesianImpedanceGoal()
#        if stamp != None:
#            action_impedance_goal.imp_trj.header.stamp = stamp
#        else:
#            action_impedance_goal.imp_trj.header.stamp = rospy.Time.now() + rospy.Duration(0.2)
#        i = 0
#        for k in k_n:
#            action_impedance_goal.imp_trj.points.append(CartesianImpedanceTrajectoryPoint(
#            rospy.Duration(t_n[i]),
#            CartesianImpedance(self.wrenchKDLtoROS(k), damping)))
#        self.action_impedance_client[prefix].send_goal(action_impedance_goal)

#    def moveImpedanceTrajLeft(self, k_n, t_n, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        self.moveImpedanceTraj(self, "left", k_n, t_n, stamp=stamp, damping=damping)

#    def moveImpedanceTrajRight(self, k_n, t_n, stamp=None, damping=Wrench(Vector3(0.7, 0.7, 0.7),Vector3(0.7, 0.7, 0.7))):
#        self.moveImpedanceTraj(self, "right", k_n, t_n, stamp=stamp, damping=damping)

#    def waitForImpedance(self, prefix):
#        self.action_impedance_client[prefix].wait_for_result()
#        result = self.action_impedance_client[prefix].get_result()
#        if result.error_code != 0:
#            print "waitForImpedance(" + prefix + "): action failed with error_code=" + str(result.error_code)
#        return result.error_code

#    def waitForImpedanceLeft(self):
#        return self.waitForImpedance("left")

#    def waitForImpedanceRight(self):
#        return self.waitForImpedance("right")

#    def moveJoint(self, q_dest, joint_names, time, start_time=0.2, position_tol=5.0/180.0 * math.pi):
#        goal = FollowJointTrajectoryGoal()
#        goal.trajectory.joint_names = self.body_joint_names
#
#        vel = []
#        q_dest_all = []
#        for joint_name in self.body_joint_names:
#            if joint_name in joint_names:
#                q_idx = joint_names.index(joint_name)
#                q_dest_all.append(q_dest[q_idx])
#                vel.append(0)
#            else:
#                q_dest_all.append(self.js_pos[joint_name])
#                vel.append(0)
#
#        goal.trajectory.points.append(JointTrajectoryPoint(q_dest_all, vel, [], [], rospy.Duration(time)))
#        #velocity_tol = 5.0/180.0 * math.pi
#        #acceleration_tol = 1.0/180.0 * math.pi
#        for joint_name in goal.trajectory.joint_names:
#            goal.path_tolerance.append(JointTolerance(joint_name, position_tol, 0, 0))#velocity_tol, acceleration_tol))
#        goal.trajectory.header.stamp = rospy.Time.now() + rospy.Duration(start_time)
#        self.action_joint_traj_client.send_goal(goal)

    def maxJointTrajLen(self):
        return 50

    def moveJointTraj(self, traj, joint_names, start_time=0.2, position_tol=5.0/180.0 * math.pi, velocity_tol=5.0/180.0*math.pi):
        goal = FollowJointTrajectoryGoal()
        goal.trajectory.joint_names = self.body_joint_names

        pos = traj[0]
        vel = traj[1]
        acc = traj[2]
        dti = traj[3]
        time = 0.0

        for node_idx in range(0, len(pos)):
            time += dti[node_idx]
            q_dest_all = []
            vel_dest_all = []

            for joint_name in self.body_joint_names:
                if joint_name in joint_names:
                    q_idx = joint_names.index(joint_name)
                    q_dest_all.append(pos[node_idx][q_idx])
                    if vel != None:
                        vel_dest_all.append(vel[node_idx][q_idx])
                    else:
                        vel_dest_all.append(0)
                else:
                    q_dest_all.append(self.js_pos[joint_name])
                    vel_dest_all.append(0)

            goal.trajectory.points.append(JointTrajectoryPoint(q_dest_all, vel_dest_all, [], [], rospy.Duration(time)))

        position_tol = position_tol
        velocity_tol = 5.0/180.0 * math.pi
        acceleration_tol = 5.0/180.0 * math.pi
        for joint_name in goal.trajectory.joint_names:
            goal.path_tolerance.append(JointTolerance(joint_name, position_tol, velocity_tol, acceleration_tol))
        goal.trajectory.header.stamp = rospy.Time.now() + rospy.Duration(start_time)
        self.action_joint_traj_client.send_goal(goal)
        return True

    def moveJoint(self, q_dest, joint_names, time, start_time=0.2, position_tol=5.0/180.0 * math.pi, velocity_tol=5.0/180.0*math.pi):
        if joint_names == None:
            if not type(q_dest) is dict:
                print "ERROR: wrong data type passed to moveJoint: ", type(q_dest)
                return False
            joint_names = []
            q_dest_list = []
            for name in q_dest:
                joint_names.append(name)
                q_dest_list.append(q_dest[name])
            return self.moveJointTraj(([q_dest_list], None, None, [time]), joint_names, start_time=start_time, position_tol=position_tol, velocity_tol=velocity_tol)
        else:
            return self.moveJointTraj(([q_dest], None, None, [time]), joint_names, start_time=start_time, position_tol=position_tol, velocity_tol=velocity_tol)

    def waitForJoint(self):
        self.action_joint_traj_client.wait_for_result()
        result = self.action_joint_traj_client.get_result()
        if result.error_code != 0:
            print "waitForJoint(): action failed with error_code=" + str(result.error_code)
        return result.error_code

    def moveHeadTraj(self, traj, start_time=0.2, position_tol=5.0/180.0 * math.pi, velocity_tol=5.0/180.0*math.pi):
        goal = FollowJointTrajectoryGoal()
        goal.trajectory.joint_names = ["head_pan_joint", "head_tilt_joint"]

        pos = traj[0]
        vel = traj[1]
        acc = traj[2]
        dti = traj[3]
        time = 0.0

        for node_idx in range(0, len(pos)):
            time += dti[node_idx]
            q_dest_all = pos[node_idx]
            if vel != None:
                vel_dest_all = vel[node_idx]
            else:
                vel_dest_all = []
            goal.trajectory.points.append(JointTrajectoryPoint(q_dest_all, vel_dest_all, [], [], rospy.Duration(time)))

        position_tol = position_tol
        velocity_tol = velocity_tol
        acceleration_tol = 5.0/180.0 * math.pi
        for joint_name in goal.trajectory.joint_names:
            goal.path_tolerance.append(JointTolerance(joint_name, position_tol, velocity_tol, acceleration_tol))
        goal.trajectory.header.stamp = rospy.Time.now() + rospy.Duration(start_time)
        self.action_head_joint_traj_client.send_goal(goal)
        return True

    def moveHead(self, q_dest, time, start_time=0.2, position_tol=5.0/180.0 * math.pi, velocity_tol=5.0/180.0*math.pi):
        return self.moveHeadTraj(([q_dest], None, None, [time]), start_time=start_time, position_tol=position_tol, velocity_tol=velocity_tol)

    def waitForHead(self):
        self.action_head_joint_traj_client.wait_for_result()
        result = self.action_head_joint_traj_client.get_result()
        if result.error_code != 0:
            print "waitForHead(): action failed with error_code=" + str(result.error_code)
        return result.error_code

    def stopArm(self, prefix):
        try:
            self.action_cart_traj_client[prefix].cancel_goal()
        except:
            pass

    def stopArmLeft(self):
        self.stopArm("left")

    def stopArmRight(self):
        self.stopArm("right")

    def emergencyStop(self):
        self.stopArmLeft()
        self.stopArmRight()
        self.emergency_stop_active = True
        print "emergency stop"

    def checkStopCondition(self, t=0.0):

        end_t = rospy.Time.now()+rospy.Duration(t+0.0001)
        while rospy.Time.now()<end_t:
            if rospy.is_shutdown():
                self.emergencyStop()
                print "emergency stop: interrupted  %s  %s"%(self.getMaxWrench(), self.wrench_tab_index)
                self.failure_reason = "user_interrupt"
                rospy.sleep(1.0)
            if self.wrench_emergency_stop:
                self.emergencyStop()
                print "too big wrench"
                self.failure_reason = "too_big_wrench"
                rospy.sleep(1.0)

#            if (self.action_cart_traj_client != None) and (self.action_cart_traj_client.gh) and ((self.action_cart_traj_client.get_state()==GoalStatus.REJECTED) or (self.action_cart_traj_client.get_state()==GoalStatus.ABORTED)):
#                state = self.action_cart_traj_client.get_state()
#                result = self.action_cart_traj_client.get_result()
#                self.emergencyStop()
#                print "emergency stop: traj_err: %s ; %s ; max_wrench: %s   %s"%(state, result, self.getMaxWrench(), self.wrench_tab_index)
#                self.failure_reason = "too_big_wrench_trajectory"
#                rospy.sleep(1.0)

#            if (self.action_tool_client.gh) and ((self.action_tool_client.get_state()==GoalStatus.REJECTED) or (self.action_tool_client.get_state()==GoalStatus.ABORTED)):
#                state = self.action_tool_client.get_state()
#                result = self.action_tool_client.get_result()
#                self.emergencyStop()
#                print "emergency stop: tool_err: %s ; %s ; max_wrench: %s   %s"%(state, result, self.getMaxWrench(), self.wrench_tab_index)
#                self.failure_reason = "too_big_wrench_tool"
#                rospy.sleep(1.0)
            rospy.sleep(0.01)
        return self.emergency_stop_active

    # ex.
    # q = (120.0/180.0*numpy.pi, 120.0/180.0*numpy.pi, 40.0/180.0*numpy.pi, 180.0/180.0*numpy.pi)
    # robot.move_hand_client("right", q)
#    def move_hand_client(self, q, v=(1.2, 1.2, 1.2, 1.2), t=(2000.0, 2000.0, 2000.0, 2000.0)):
#        rospy.wait_for_service('/' + self.prefix + '_hand/move_hand')
#        try:
#            move_hand = rospy.ServiceProxy('/' + self.prefix + '_hand/move_hand', BHMoveHand)
#            resp1 = move_hand(q[0], q[1], q[2], q[3], v[0], v[1], v[2], v[3], t[0], t[1], t[2], t[3])
#        except rospy.ServiceException, e:
#            print "Service call failed: %s"%e

    moveHand_action_error_codes_names = {
        0:"SUCCESSFUL",
        -1:"INVALID_DOF_NAME",
        -2:"INVALID_GOAL",
        -3:"CURRENT_LIMIT",
        -4:"PRESSURE_LIMIT",
        -5:"RESET_IS_ACTIVE"}

    def moveHand(self, q, v, t, maxPressure, hold=False, prefix="right"):
        action_goal = BHMoveGoal()
        action_goal.name = [prefix+"_HandFingerOneKnuckleTwoJoint", prefix+"_HandFingerTwoKnuckleTwoJoint", prefix+"_HandFingerThreeKnuckleTwoJoint", prefix+"_HandFingerOneKnuckleOneJoint"]
        action_goal.q = q
        action_goal.v = v
        action_goal.t = t
        action_goal.maxPressure = maxPressure
        action_goal.reset = False
        if hold == True:
            action_goal.hold = 1
        else:
            action_goal.hold = 0
        self.action_move_hand_client[prefix].send_goal(action_goal)

    def moveHandLeft(self, q, v, t, maxPressure, hold=False):
        self.moveHand(q, v, t, maxPressure, hold=hold, prefix="left")

    def moveHandRight(self, q, v, t, maxPressure, hold=False):
        self.moveHand(q, v, t, maxPressure, hold=hold, prefix="right")

    def resetHand(self, prefix="right"):
        action_goal = BHMoveGoal()
        action_goal.reset = True
        self.action_move_hand_client[prefix].send_goal(action_goal)

    def resetHandLeft(self):
        self.resetHand(prefix="left")

    def resetHandRight(self):
        self.resetHand(prefix="right")

    def waitForHand(self, prefix="right"):
        self.action_move_hand_client[prefix].wait_for_result()
        result = self.action_move_hand_client[prefix].get_result()
        if result.error_code != 0:
            print "waitForHand(" + prefix + "): action failed with error_code=" + str(result.error_code) + " (" + self.moveHand_action_error_codes_names[result.error_code] + ")"
        return result.error_code

    def waitForHandLeft(self):
        return self.waitForHand(prefix="left")

    def waitForHandRight(self):
        return self.waitForHand(prefix="right")

    def hasContact(self, threshold, print_on_false=False):
        if self.T_F_C != None:
            return True
        return False

    def getKDLtf(self, base_frame, frame):
        pose = self.listener.lookupTransform(base_frame, frame, rospy.Time(0))
        return pm.fromTf(pose)

    def getAllLinksTf(self, base_frame):
        result = {}
        for l in self.all_links:
            try:
                result[l.name] = self.getKDLtf(base_frame, l.name)
            except:
                result[l.name] = None
        return result

    fingers_links = {
        (0,0):'_HandFingerOneKnuckleOneLink',
        (0,1):'_HandFingerOneKnuckleTwoLink',
        (0,2):'_HandFingerOneKnuckleThreeLink',
        (1,0):'_HandFingerTwoKnuckleOneLink',
        (1,1):'_HandFingerTwoKnuckleTwoLink',
        (1,2):'_HandFingerTwoKnuckleThreeLink',
        (2,1):'_HandFingerThreeKnuckleTwoLink',
        (2,2):'_HandFingerThreeKnuckleThreeLink',
    }

    frames = {
        'Wo':'world',
        'B':'torso_base',
        'Wr':'right_arm_7_link',
        'Wl':'left_arm_7_link',
        'Wright':'right_arm_7_link',
        'Wleft':'left_arm_7_link',
        'Gr':'right_HandGripLink',
        'Gl':'left_HandGripLink',
        'Tr':'right_arm_tool',
        'Tl':'left_arm_tool',
        'Tright':'right_arm_tool',
        'Tleft':'left_arm_tool',
        'Fr00':'right_HandFingerOneKnuckleOneLink',
        'Fr01':'right_HandFingerOneKnuckleTwoLink',
        'Fr02':'right_HandFingerOneKnuckleThreeLink',
        'Fr10':'right_HandFingerTwoKnuckleOneLink',
        'Fr11':'right_HandFingerTwoKnuckleTwoLink',
        'Fr12':'right_HandFingerTwoKnuckleThreeLink',
        'Fr21':'right_HandFingerThreeKnuckleTwoLink',
        'Fr22':'right_HandFingerThreeKnuckleThreeLink',
        'Fl00':'left_HandFingerOneKnuckleOneLink',
        'Fl01':'left_HandFingerOneKnuckleTwoLink',
        'Fl02':'left_HandFingerOneKnuckleThreeLink',
        'Fl10':'left_HandFingerTwoKnuckleOneLink',
        'Fl11':'left_HandFingerTwoKnuckleTwoLink',
        'Fl12':'left_HandFingerTwoKnuckleThreeLink',
        'Fl21':'left_HandFingerThreeKnuckleTwoLink',
        'Fl22':'left_HandFingerThreeKnuckleThreeLink',
    }

    def getTf(self, frame_from, frame_to):
        if frame_from in self.frames and frame_to in self.frames:
            return self.getKDLtf( self.frames[frame_from], self.frames[frame_to] )
        return None

    def handleEmergencyStop(self):
        if self.emergency_stop_active:
            ch = '_'
            while (ch != 'e') and (ch != 'n') and (ch != 'r'):
                ch = raw_input("Emergency stop active... (e)xit, (n)ext case, (r)epeat case: ")
            if ch == 'e':
                exit(0)
            if ch == 'n':
                self.action = "next"
                self.index += 1
            if ch == 'r':
                self.action = "repeat"
            self.updateTransformations()
            print "moving desired pose to current pose"
            self.emergency_stop_active = False
            self.moveWrist(self.T_B_W, 2.0, Wrench(Vector3(25,25,25), Vector3(5,5,5)))
            self.checkStopCondition(2.0)
            return True
        return False

    def getMovementTime(self, T_B_Wd, max_v_l = 0.1, max_v_r = 0.2):
        self.updateTransformations()
        twist = PyKDL.diff(self.T_B_W, T_B_Wd, 1.0)
        v_l = twist.vel.Norm()
        v_r = twist.rot.Norm()
#        print "v_l: %s   v_r: %s"%(v_l, v_r)
        f_v_l = v_l/max_v_l
        f_v_r = v_r/max_v_r
        if f_v_l > f_v_r:
            duration = f_v_l
        else:
            duration = f_v_r
        if duration < 0.2:
            duration = 0.2
        return duration

    def getMovementTime2(self, T_B_Wd1, T_B_Wd2, max_v_l = 0.1, max_v_r = 0.2):
        twist = PyKDL.diff(T_B_Wd1, T_B_Wd2, 1.0)
        v_l = twist.vel.Norm()
        v_r = twist.rot.Norm()
        print "v_l: %s   v_r: %s"%(v_l, v_r)
        f_v_l = v_l/max_v_l
        f_v_r = v_r/max_v_r
        if f_v_l > f_v_r:
            duration = f_v_l
        else:
            duration = f_v_r
        if duration < 0.5:
            duration = 0.5
        return duration

