#!/usr/bin/env python
import rospy
import sys
import math
import time
from statemachine import StateMachine, State
from robot.robot import Robot
from std_msgs.msg import String
from my_sys import log, SysCheck, logInOne
from methods.chase import Chase
from methods.attack import Attack
from methods.behavior import Behavior
from dynamic_reconfigure.server import Server
from strategy.cfg import StrategyConfig
import dynamic_reconfigure.client

class Core(Robot, StateMachine):
  def __init__(self, robot_num, sim = False):
    super(Core, self).__init__(robot_num, sim)
    StateMachine.__init__(self)
    self.CC  = Chase()
    self.AC  = Attack()
    self.BC  = Behavior()
    self.sim = sim

  idle   = State('Idle', initial = True)
  chase  = State('Chase')
  attack = State('Attack')
  shoot  = State('Shoot')
  orbit  = State('Orbit')
  point  = State('Point')

  toIdle   = chase.to(idle) | attack.to(idle)  | orbit.to(idle) | point.to(idle) | idle.to.itself()
  toChase  = idle.to(chase) | attack.to(chase) | chase.to.itself() | orbit.to(chase) | point.to(chase)
  toAttack = chase.to(attack) | attack.to.itself() | shoot.to(attack) | orbit.to(attack)
  toShoot  = attack.to(shoot)
  toOrbit  = chase.to(orbit) | orbit.to.itself()
  toPoint  = point.to.itself() | idle.to(point)

  def on_toIdle(self):
    for i in range(0, 10):
        self.MotionCtrl(0,0,0)
    log("To Idle1")

  def on_toChase(self, t, side, method = "Classic"):
    if method == "Classic":
      x, y, yaw = self.CC.ClassicRounding(t[side]['ang'],\
                                          t['ball']['dis'],\
                                          t['ball']['ang'])
      self.MotionCtrl(x, y, yaw)
    elif method == "Straight":
      x, y, yaw = self.CC.StraightForward(t['ball']['dis'], t['ball']['ang'])
      self.MotionCtrl(x, y, yaw)

  def on_toAttack(self, t, side ,l,a):
    x, y, yaw = self.AC.ClassicAttacking(t[side]['dis'], t[side]['ang'], l['ranges'], l['angle']['increment'],a)
    self.MotionCtrl(x, y, yaw)
    log('attack')

  def on_toShoot(self, power, pos):
    self.RobotShoot(power, pos)

  def on_toOrbit(self, t, side):
    x, y, yaw = self.CC.Orbit(t[side]['ang'])
    self.MotionCtrl(x, y, yaw, True)

  def on_toPoint(self, tx, ty, tyaw):
    x, y, yaw, remaining = self.BC.Go2Point(tx, ty, tyaw)
    self.MotionCtrl(x, y, yaw)
    return remaining

  def PubCurrentState(self):
    self.RobotStatePub(self.current_state.identifier)

  def CheckBallHandle(self):
    return self.RobotBallHandle()

class Strategy(object):
  def __init__(self, num, sim=False):
    rospy.init_node('core', anonymous=True)
    self.rate = rospy.Rate(1000)

    self.robot = Core(num, sim)

    dsrv = Server(StrategyConfig, self.Callback)
    self.dclient = dynamic_reconfigure.client.Client("core", timeout=30, config_callback=None)

  def RunStatePoint(self, state):
    if state == "Kick_Off" and self.side == "Yellow" :
      c = self.robot.toPoint(-60, 0, 0)
    elif state == "Kick_Off" and self.side == "Blue" :
      c = self.robot.toPoint(60, 0, 180)
    elif state == "Free_Kick" :
      c = self.robot.toPoint(100, 100, 90)
    elif state == "Free_Ball" :
      c = self.robot.toPoint(100, -100, 180)
    elif state == "Throw_In" :
      c = self.robot.toPoint(-100, -100, 270)
    elif state == "Coner_Kick":
      c = self.robot.toPoint(300, 200, 45)
    elif state == "Penalty_Kick" :
      c = self.robot.toPoint(-100, 100, 135)
    elif state == "Run_Specific_Point" :
      c = self.robot.toPoint(self.run_x, self.run_y, self.run_yaw)
    else:
      print("ummmm")

    if c:
      self.robot.toIdle()
      self.dclient.update_configuration({"run_point": False})

  def Chase(self, t):
    if self.strategy_mode == "Defense":
      return self.robot.toChase(t, self.opp_side, "Classic")
    elif self.strategy_mode == "Attack":
      return self.robot.toChase(t, self.opp_side, "Straight")

  def main(self):

    while not rospy.is_shutdown():

      self.robot.PubCurrentState()

      targets = self.robot.GetObjectInfo()
      position = self.robot.GetRobotInfo()
      laser = self.robot.GetObstacleInfo()

      if targets is None or targets['ball']['ang'] == 999 and self.game_start: # Can not find ball when starting
        print("Can not find ball")
        self.robot.toIdle()
      else:
        if not self.robot.is_idle and not self.run_point and not self.game_start:
          self.robot.toIdle()

        if self.robot.is_idle:
          if self.game_start:
            self.Chase(targets)
          elif self.run_point:
            self.RunStatePoint(self.game_state)

        if self.robot.is_chase:
          if self.robot.CheckBallHandle():
            if self.strategy_mode == "Attack":
              self.robot.toOrbit(targets, self.opp_side)
            elif self.strategy_mode == "Defense":
              self.robot.toAttack(targets, self.opp_side , laser ,a)
          else:
            self.Chase(targets)

        if self.robot.is_orbit:
          if abs(targets[self.opp_side]['ang']) < self.orb_attack_ang :
            self.robot.toAttack(targets, self.opp_side , laser ,a)
          elif not self.robot.CheckBallHandle():
            self.Chase(targets)
          else:
            self.robot.toOrbit(targets, self.opp_side)

        if self.robot.is_attack:
          if not self.robot.CheckBallHandle():
            self.Chase(targets)
          elif abs(targets[self.opp_side]['ang']) < self.atk_shoot_ang and targets[self.opp_side]['dis'] < 200:
            self.robot.toShoot(3, 1)
          else:
            self.robot.toAttack(targets, self.opp_side , laser ,a)

        if self.robot.is_shoot:
          self.robot.toAttack(targets, self.opp_side , laser ,a)

      ## Run point
      if self.robot.is_point:
        self.RunStatePoint(self.game_state)

      if rospy.is_shutdown():
        log('shutdown')
        break

      self.rate.sleep()

  def Callback(self, config, level):
    self.game_start = config['game_start']
    self.game_state = config['game_state']
    self.run_point  = config['run_point']
    self.side       = config['our_goal']
    self.opp_side   = 'Yellow' if config['our_goal'] == 'Blue' else 'Blue'
    self.run_x      = config['run_x']
    self.run_y      = config['run_y']
    self.run_yaw    = config['run_yaw']
    self.strategy_mode = config['strategy_mode']
    self.orb_attack_ang  = config['orb_attack_ang']
    self.atk_shoot_ang  = config['atk_shoot_ang']
   #self.ROTATE_V_ang   = config['ROTATE_V_ang']
    self.remaining_range_v   = config['remaining_range_v']
    self.remaining_range_yaw = config['remaining_range_yaw']

    self.robot.ChangeVelocityRange(config['minimum_v'], config['maximum_v'])
    self.robot.ChangeAngularVelocityRange(config['minimum_w'], config['maximum_w'])
    self.robot.ChangeBallhandleCondition(config['ballhandle_dis'], config['ballhandle_ang'])

    self.run_point = config['run_point']

    return config

if __name__ == '__main__':
  try:
    if SysCheck(sys.argv[1:]) == "Native Mode":
      log("Start Native")
      s = Strategy(1, False)
      a=0
    elif SysCheck(sys.argv[1:]) == "Simulative Mode":
      log("Start Sim")
      s = Strategy(1, True)
      a=1
    # s.main(sys.argv[1:])
    s.main()
  except rospy.ROSInterruptException:
    pass
