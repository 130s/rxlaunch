#! /usr/bin/env python

import sys, os, os.path
import signal
import time
import uuid
import logging
from PySide import QtCore, QtGui

from ros import roslaunch
import roslib
import rospy

_ID = '/rxlaunch'

def handle_sigint(*args):
    sys.stderr.write("\rSIGINT")
    QtGui.QApplication.quit()

class StatusIndicator(QtGui.QLabel):
    def __init__(self, *args):
        super(StatusIndicator, self).__init__(*args)
        self.set_stopped()

    def set_running(self):
        self.setPixmap(self.style().standardIcon(QtGui.QStyle.SP_DialogApplyButton).pixmap(16))

    def set_starting(self):
        self.setPixmap(self.style().standardIcon(QtGui.QStyle.SP_DialogResetButton).pixmap(16))
    
    def set_stopping(self):
        self.setPixmap(self.style().standardIcon(QtGui.QStyle.SP_DialogResetButton).pixmap(16))

    def set_stopped(self):
        self.setText(" ")

    def set_died(self):
        self.setPixmap(self.style().standardIcon(QtGui.QStyle.SP_MessageBoxCritical).pixmap(16))

class NodeProxy(object):
    __slots__ = ['run_id', 'master_uri', 'config', 'process']
    
    def __init__(self, run_id, master_uri, config):
        self.run_id = run_id
        self.master_uri = master_uri
        self.config = config

        self.recreate_process()

    # LocalProcess.is_alive() does not do what you would expect
    def is_running(self):
        return self.process.started and self.process.is_alive()

    def has_died(self):
        return self.process.started and not self.process.stopped and not self.process.is_alive()

    def recreate_process(self):
        self.process = roslaunch.nodeprocess.create_node_process(self.run_id, self.config, self.master_uri)

class NodeGui(object):
    __slots__ = ['status_label', 'respawn_toggle', 'spawn_count_label', 'launch_prefix_edit']

    def __init__(self, status_label, respawn_toggle, spawn_count_label, launch_prefix_edit):
        self.status_label = status_label
        self.respawn_toggle = respawn_toggle
        self.spawn_count_label = spawn_count_label
        self.launch_prefix_edit = launch_prefix_edit

# Provides callback functions for the start and stop buttons
class NodeController(object):
    __slots__ = ['proxy', 'gui']

    def __init__(self, proxy, gui):
        self.proxy = proxy
        self.gui = gui

    def start(self, restart=True):
        if self.proxy.is_running():
            if not restart:
                return
            self.gui.status_label.set_stopping()
            self.proxy.process.stop()

        # If the launch_prefix has changed, then the process must be re-created
        if self.proxy.config.launch_prefix != self.gui.launch_prefix_edit.text():
            self.proxy.config.launch_prefix = self.gui.launch_prefix_edit.text()
            self.proxy.recreate_process()
            #self.proxy.process = roslaunch.nodeprocess.create_node_process(self.run_id, self.config, self.master_uri)
            
            
        self.gui.status_label.set_starting()
        self.proxy.process.start()
        self.gui.status_label.set_running()
        self.gui.spawn_count_label.setText("(%d)" % self.proxy.process.spawn_count)

    def stop(self):
        if self.proxy.is_running():
            self.gui.status_label.set_stopping()
            self.proxy.process.stop()
            self.gui.status_label.set_stopped()


    def check_process_status(self):
        if self.proxy.has_died():
            print "Process died: %s" % self.proxy.process.name
            self.proxy.process.stop()
            if self.proxy.process.exit_code == 0:
                self.gui.status_label.set_stopped()
            else:
                self.gui.status_label.set_died()

            # Checks if it should be respawned
            if self.gui.respawn_toggle.isChecked():
                print "Respawning process: %s" % self.proxy.process.name
                self.gui.status_label.set_starting()
                self.proxy.process.start()
                self.gui.status_label.set_running()
                self.gui.spawn_count_label.setText("(%d)" % self.proxy.process.spawn_count)

class NamesSurrogate(object):
    """
    Because some functions in roslib.names cannot be referred in the original rxlaunch code, 
    the codes of those function are copied here. This class should not be used for 
    any other purpose than to be used within this .py file.

    :author: Isaac Saito
    """

    PRIV_NAME = '~' 
    SEP = '/' 

    @staticmethod
    def is_global(name):
        """
        Test if name is a global graph resource name. 116 117 @param name: must be a legal name in canonical form 118 @type name: str 119 @return: True if name is a globally referenced name (i.e. /ns/name) 120 @rtype: bool
        """ 
        return name and name[0] == NamesSurrogate.SEP 

    @staticmethod
    def is_private(name):
        """ 126 Test if name is a private graph resource name. 127 128 @param name: must be a legal name in canonical form 129 @type name: str 130 @return bool: True if name is a privately referenced name (i.e. ~name) 131 """ 
        return name and name[0] == NamesSurrogate.PRIV_NAME 

    @staticmethod
    def ns_join(ns, name):
        """ 
        Taken from http://ros.org/rosdoclite/groovy/api/roslib/html/python/roslib.names-pysrc.html#ns_join 
        since roslib.names is not found for some reason, and also the entire module seems deprecated.

        Join a namespace and name. If name is unjoinable (i.e. ~private or 162 /global) it will be returned without joining 163 164 @param ns: namespace ('/' and '~' are both legal). If ns is the empty string, name will be returned. 165 @type ns: str 166 @param name str: a legal name 167 @return str: name concatenated to ns, or name if it is 168 unjoinable. 169 @rtype: str 170 
        """ 
        if NamesSurrogate.is_private(name) or NamesSurrogate.is_global(name): 
            return name
        if ns == NamesSurrogate.PRIV_NAME:
            return NamesSurrogate.PRIV_NAME + name
        if not ns:
            return name
        if ns[-1] == NamesSurrogate.SEP:
            return ns + name
        return ns + NamesSurrogate.SEP + name 

class RxlaunchApp(QtGui.QDialog):

    def __init__(self, args, parent=None):
        super(RxlaunchApp, self).__init__(parent)
        try:
            launchfile = args[1]
        except IndexError:
            sys.stderr.write("Please give a launch file\n")
            sys.exit(1)

        self.run_id = None
        uuid = roslaunch.rlutil.get_or_generate_uuid(self.run_id, True)
        #uuid = 'fake-uuid'
        print "UUID:", uuid
        roslaunch.configure_logging(uuid)
        roslaunch_logger = logging.getLogger("roslaunch")
        roslaunch_logger.setLevel(logging.DEBUG)


        self.config = roslaunch.config.load_config_default([launchfile], 11311)
        print self.config.summary()
        print "MASTER", self.config.master.uri

        self._load_parameters()
        
        # Buttons in the header
        self.button_start_all = QtGui.QPushButton("Start all")
        self.button_start_all.clicked.connect(self.start_all)
        self.button_stop_all = QtGui.QPushButton("Stop all")
        self.button_stop_all.clicked.connect(self.stop_all)
        header_buttons = QtGui.QHBoxLayout()
        header_buttons.addWidget(self.button_start_all)
        header_buttons.addWidget(self.button_stop_all)
        header_buttons_box = QtGui.QGroupBox()
        header_buttons_box.setLayout(header_buttons)

        # Creates the process grid
        self.node_controllers = []
        process_layout = QtGui.QGridLayout()
        for i, node_config in enumerate(self.config.nodes):
            proxy = NodeProxy(self.run_id, self.config.master.uri, node_config)

            # TODO: consider using QIcon.fromTheme()
            status = StatusIndicator()
            start_button = QtGui.QPushButton(self.style().standardIcon(QtGui.QStyle.SP_MediaPlay), "")
            start_button.setIconSize(QtCore.QSize(16, 16))
            stop_button = QtGui.QPushButton(self.style().standardIcon(QtGui.QStyle.SP_MediaStop), "")
            stop_button.setIconSize(QtCore.QSize(16, 16))
            respawn_toggle = QtGui.QToolButton()
            respawn_toggle.setIcon(self.style().standardIcon(QtGui.QStyle.SP_BrowserReload))
            respawn_toggle.setIconSize(QtCore.QSize(16, 16))
            respawn_toggle.setCheckable(True)
            respawn_toggle.setChecked(proxy.config.respawn)
            spawn_count_label = QtGui.QLabel("(0)")
            launch_prefix_edit = QtGui.QLineEdit(proxy.config.launch_prefix)

            gui = NodeGui(status, respawn_toggle, spawn_count_label, launch_prefix_edit)

            node_controller = NodeController(proxy, gui)
            self.node_controllers.append(node_controller)
    
            #TODO(Isaac) These need to be commented in in order to function as originally intended.
            #start_button.clicked.connect(node_controller.start) 
            #stop_button.clicked.connect(node_controller.stop)

            #resolved_node_name = roslib.names.ns_join(proxy.config.namespace, proxy.config.name)
            rospy.loginfo('loop #%d proxy.config.namespace=%s proxy.config.name=%s', i, proxy.config.namespace, proxy.config.name)
            resolved_node_name = NamesSurrogate.ns_join(proxy.config.namespace, proxy.config.name)

            j = 0
            process_layout.addWidget(status, i, j)
            process_layout.setColumnMinimumWidth(j, 20)                           ;  j += 1
            process_layout.addWidget(QtGui.QLabel(resolved_node_name), i, j)      ;  j += 1
            process_layout.addWidget(spawn_count_label, i, j)                     
            process_layout.setColumnMinimumWidth(j, 30)                           ;  j += 1
            process_layout.setColumnMinimumWidth(j, 30)                           ;  j += 1  # Spacer
            process_layout.addWidget(start_button, i, j)                          ;  j += 1
            process_layout.addWidget(stop_button, i, j)                           ;  j += 1
            process_layout.addWidget(respawn_toggle, i, j)                        ;  j += 1
            process_layout.setColumnMinimumWidth(j, 20)                           ;  j += 1  # Spacer
            process_layout.addWidget(QtGui.QLabel(proxy.config.package), i, j)    ;  j += 1
            process_layout.addWidget(QtGui.QLabel(proxy.config.type), i, j)       ;  j += 1
            process_layout.addWidget(launch_prefix_edit, i, j)                    ;  j += 1


        process_scroll = QtGui.QScrollArea()
        #process_scroll.setMinimumWidth(process_layout.sizeHint().width())  # Doesn't work properly.  Too small
        process_widget = QtGui.QWidget()
        process_widget.setLayout(process_layout)
        process_scroll.setWidget(process_widget)
        
        # Creates the log display area
        self.log_text = QtGui.QPlainTextEdit()

        # Sets up the overall layout
        process_log_splitter = QtGui.QSplitter()
        process_log_splitter.setOrientation(QtCore.Qt.Vertical)
        process_log_splitter.addWidget(process_scroll)
        process_log_splitter.addWidget(self.log_text)
        main_layout = QtGui.QVBoxLayout()
        main_layout.addWidget(header_buttons_box, stretch=0)
        #main_layout.addWidget(process_scroll, stretch=10)
        #main_layout.addWidget(self.log_text, stretch=30)
        main_layout.addWidget(process_log_splitter)
        self.setLayout(main_layout)
        
    # Stolen from ROSLaunchRunner
    def _load_parameters(self):
        """
        Load parameters onto the parameter server
        """
        #self.logger.info("load_parameters starting ...")
        config = self.config
        param_server = config.master.get()
        p = None
        try:
            # multi-call style xmlrpc
            param_server_multi = config.master.get_multi()

            # clear specified parameter namespaces
            # #2468 unify clear params to prevent error
            for p in roslaunch.launch._unify_clear_params(config.clear_params):
                if param_server.hasParam(_ID, p)[2]:
                    #printlog("deleting parameter [%s]"%p)
                    param_server_multi.deleteParam(_ID, p)
            r = param_server_multi()
            for code, msg, _ in r:
                if code != 1:
                    raise roslaunch.RLException("Failed to clear parameter: %s"%(msg))

            # multi-call objects are not reusable
            param_server_multi = config.master.get_multi()            
            for p in config.params.itervalues():
                # suppressing this as it causes too much spam
                #printlog("setting parameter [%s]"%p.key)
                param_server_multi.setParam(_ID, p.key, p.value)
            r  = param_server_multi()
            for code, msg, _ in r:
                if code != 1:
                    raise roslaunch.RLException("Failed to set parameter: %s"%(msg))
        except roslaunch.RLException:
            raise
        except Exception, e:
            #printerrlog("load_parameters: unable to set parameters (last param was [%s]): %s"%(p,e))
            print("load_parameters: unable to set parameters (last param was [%s]): %s"%(p,e))
            raise #re-raise as this is fatal
        #self.logger.info("... load_parameters complete")            
        print("... load_parameters complete")            


    def start_all(self):
        print "Starting all nodes"
        for n in self.node_controllers:
            n.start(restart=False)

    def stop_all(self):
        print "Stopping all nodes"
        for n in self.node_controllers:
            n.stop()

    def check_process_statuses(self):
        for n in self.node_controllers:
            n.check_process_status()


def main():
    app = QtGui.QApplication(sys.argv)

    # Sets up signal handling so SIGINT closes the application,
    # following the solution given at [1].  Sets up a custom signal
    # handler, and ensures that the Python interpreter runs
    # occasionally so the signal is handled.  The email thread at [2]
    # explains why this is necessary.
    #
    # [1] http://stackoverflow.com/questions/4938723/#4939113
    # [2] http://www.mail-archive.com/pyqt@riverbankcomputing.com/msg13757.html
    signal.signal(signal.SIGINT, handle_sigint)
    timer = QtCore.QTimer()
    timer.start(250)
    timer.timeout.connect(lambda: None)  # Forces the interpreter to run every 250ms

    form = RxlaunchApp(sys.argv)
    status_checker_timer = QtCore.QTimer()
    status_checker_timer.timeout.connect(form.check_process_statuses)
    status_checker_timer.start(100)
    form.show()
    
    exit_code = -1
    try:
        exit_code = app.exec_()
    finally:
        form.stop_all()
    sys.exit(exit_code)


if __name__ == '__main__': main()
