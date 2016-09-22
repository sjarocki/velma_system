/*
 Copyright (c) 2014, Robot Control and Pattern Recognition Group, Warsaw University of Technology
 All rights reserved.
 
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:
     * Redistributions of source code must retain the above copyright
       notice, this list of conditions and the following disclaimer.
     * Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.
     * Neither the name of the Warsaw University of Technology nor the
       names of its contributors may be used to endorse or promote products
       derived from this software without specific prior written permission.
 
 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 DISCLAIMED. IN NO EVENT SHALL <COPYright HOLDER> BE LIABLE FOR ANY
 DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

#include "ft_sensor_gazebo.h"

void FtSensorGazebo::updateHook() {
    // Synchronize with gazeboUpdate()
    RTT::os::MutexLock lock(gazebo_mutex_);

    if (!data_valid_) {
        return;
    }
    port_raw_wrench_out_.write(raw_wrench_out_);
    port_slow_filtered_wrench_out_.write(slow_filtered_wrench_out_);
    port_fast_filtered_wrench_out_.write(fast_filtered_wrench_out_);
}

bool FtSensorGazebo::startHook() {
    return true;
}

bool FtSensorGazebo::configureHook() {
    Logger::In in("FtSensorGazebo::configureHook");

    joint_ = model_->GetJoint(joint_name_);
    if (joint_.get() == NULL) {
        Logger::log() << Logger::ERROR << "could not find joint \"" << joint_name_ << "\"" << Logger::endl;
        return false;
    }

    link_ = joint_->GetJointLink(0);
//    dart_bn_ = boost::dynamic_pointer_cast < gazebo::physics::DARTJoint > ( joint_ )->GetDARTJoint()->getChildBodyNode();

    if (transform_xyz_.size() != 3) {
        Logger::log() << Logger::ERROR << "wrong transform_xyz: vector size is " << transform_xyz_.size() << ", should be 3" << Logger::endl;
        return false;
    }

    if (transform_rpy_.size() != 3) {
        Logger::log() << Logger::ERROR << "wrong transform_rpy: vector size is " << transform_rpy_.size() << ", should be 3" << Logger::endl;
        return false;
    }

    T_W_S_ = KDL::Frame(KDL::Rotation::RPY(transform_rpy_[0], transform_rpy_[1], transform_rpy_[2]), KDL::Vector(transform_xyz_[0], transform_xyz_[1], transform_xyz_[2]));

    return true;
}

