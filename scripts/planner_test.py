#!/usr/bin/env python
import roslib; roslib.load_manifest('velma_task_cs_ros_interface')
import rospy

from planner.planner import *

from moveit_msgs.msg import *
from moveit_msgs.srv import *

if __name__ == "__main__":

    rospy.init_node('wrists_test', anonymous=True)

    rospy.sleep(1)

    p = Planner()

    q_map_1 = {'torso_0_joint':0.0,
        'right_arm_0_joint':-0.3,
        'right_arm_1_joint':-1.57,
        'right_arm_2_joint':1.57,
        'right_arm_3_joint':1.57,
        'right_arm_4_joint':0.0,
        'right_arm_5_joint':-1.57,
        'right_arm_6_joint':0.0,
        'left_arm_0_joint':0.3,
        'left_arm_1_joint':1.57,
        'left_arm_2_joint':-1.57,
        'left_arm_3_joint':-1.7,
        'left_arm_4_joint':0.0,
        'left_arm_5_joint':1.57,
        'left_arm_6_joint':0.0
        }

    q_map_2 = {'torso_0_joint':0.0,
        'right_arm_0_joint':0.0,
        'right_arm_1_joint':0.0,
        'right_arm_2_joint':0.0,
        'right_arm_3_joint':0.0,
        'right_arm_4_joint':0.0,
        'right_arm_5_joint':0.0,
        'right_arm_6_joint':0.0,
        'left_arm_0_joint':0.0,
        'left_arm_1_joint':0.0,
        'left_arm_2_joint':0.0,
        'left_arm_3_joint':0.0,
        'left_arm_4_joint':0.0,
        'left_arm_5_joint':0.0,
        'left_arm_6_joint':0.0
        }

    goal_constraint_1 = qMapToConstraints(q_map_1, 0.01)

    js = [None, q_map_2]
    traj, jn = p.plan(js, [goal_constraint_1], "impedance_joints", max_velocity_scaling_factor=0.1)

    print "generated trajectory of length:", len(traj[0])

