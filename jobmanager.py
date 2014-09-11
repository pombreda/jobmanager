import multiprocessing as mp
from multiprocessing.managers import SyncManager
import queue


import copy
import signal
import pickle
import traceback
import socket
from numpy.random import rand

import os
import sys

import time
import datetime
import math

import psutil



import fcntl
import termios
import struct

"""jobmanager module

Richard Hartmann 2014


This module provides an easy way to implement distributed computing
based on the python class SyncManager for remote communication
and the python module multiprocessing for local parallelism.

class SIG_handler_Loop

class Loop

class StatusBar

The class JobManager_Server will provide a server process handling the
following tasks:
    - providing a list (queue) of arguments to be processed by client processes 
    (see put_arg and args_from_list)
    - handling the results of the calculations done by the client processes
    (see process_new_result)
    - when finished (all provided arguments have been processed and returned its result)
    process the obtained results (see process_final_result)
    
The class JobManager_Client

Furthermore this module provides as Loop class to spawn a process repeating to 
call a certain function as well as a StatusBar class for the terminal. 
"""

# a mapping from the numeric values of the signals to their names used in the
# standard python module signals
signal_dict = {}
for s in dir(signal):
    if s.startswith('SIG') and s[3] != '_':
        n = getattr(signal, s)
        if n in signal_dict:
            signal_dict[n] += ('/'+s)
        else:
            signal_dict[n] = s

# a list of all names of the implemented python signals
all_signals = [s for s in dir(signal) if (s.startswith('SIG') and s[3] != '_')]

def getDateForFileName(includePID = False):
    """returns the current date-time and optionally the process id in the format
    YYYY_MM_DD_hh_mm_ss_pid
    """
    date = time.localtime()
    name = '{:d}_{:02d}_{:02d}_{:02d}_{:02d}_{:02d}'.format(date.tm_year, date.tm_mon, date.tm_mday, date.tm_hour, date.tm_min, date.tm_sec)
    if includePID:
        name += "_{}".format(os.getpid()) 
    return name

def humanize_time(secs):
    """convert second in to hh:mm:ss format
    """
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    return '{:02d}:{:02d}:{:02d}'.format(int(hours), int(mins), int(secs))

def humanize_speed(c_per_sec):
    """convert a speed in counts per second to counts per [s, min, h, d], choosing the smallest value greater zero.
    """
    scales = [60, 60, 24]
    units = ['c/s', 'c/min', 'c/h', 'c/d']
    speed = c_per_sec
    i = 0
    if speed > 0:
        while (speed < 1) and (i < len(scales)):
            speed *= scales[i]
            i += 1
        
    return "{:.1f}{}".format(speed, units[i])

class SIG_handler_Loop(object):
    """class to setup signal handling for the Loop class
    
    Note: each subprocess receives the default signal handling from it's parent.
    If the signal function from the module signal is evoked within the subprocess
    this default behavior can be overwritten.
    
    The init function receives a shared memory boolean object which will be set
    false in case of signal detection. Since the Loop class will check the state 
    of this boolean object before each repetition, the loop will stop when
    a signal was receives.
    """
    def __init__(self, shared_mem_run, sigint, sigterm, identifier, verbose=0):
        self.shared_mem_run = shared_mem_run
        self.set_signal(signal.SIGINT, sigint)
        self.set_signal(signal.SIGTERM, sigterm)
        self.verbose=verbose
        self.identifier = identifier
    def set_signal(self, sig, handler_str):
        if handler_str == 'ign':
            signal.signal(sig, self._ignore_signal)
        elif handler_str == 'stop':
            signal.signal(sig, self._stop_on_signal)
        else:
            raise TypeError("unknown signal hander string '{}'".format(handler_str))
    
    def _ignore_signal(self, signal, frame):
        pass
    def _stop_on_signal(self, signal, frame):
        if self.verbose > 0:
            print("\n{}received sig {} -> set run false".format(self.identifier, signal_dict[signal]))
        self.shared_mem_run.value = False


class Loop(object):
    """
    class to run a function periodically an seperate process.
    
    In case the called function returns True, the loop will stop.
    Otherwise a time interval given by interval will be slept before
    another execution is triggered.
    
    The shared memory variable _run (accessible via the class property run)  
    also determines if the function if executed another time. If set to False
    the execution stops.
    
    In order to catch any Errors it is advisable to instantiate this class
    using with statement as follows:
        
        with Loop(**kwargs) as my_loop:
            my_loop.start()
            ...
    """
    def __init__(self, 
                  func, 
                  args=(), 
                  interval = 1, 
                  verbose=0, 
                  sigint='stop', 
                  sigterm='stop',
                  name=None,
                  auto_kill_on_last_resort=False):
        """
        func [callable] - function to be called periodically
        
        args [tuple] - arguments passed to func when calling
        
        intervall [pos number] - time to "sleep" between each call
        
        verbose [pos integer] - specifies the level of verbosity
          [0--silent, 1--important information, 2--some debug info]
          
        sigint [string] - signal handler string to set SIGINT behavior (see below)
        
        sigterm [string] - signal handler string to set SIGTERM behavior (see below)
        
        name [string] - use this name in messages instead of the PID 
        
        auto_kill_on_last_resort [bool] - If set False (default), ask user to send SIGKILL 
        to loop process in case normal stop and SIGTERM failed. If set True, send SIDKILL
        without asking.
        
        the signal handler string may be one of the following
            ing: ignore the incoming signal
            stop: set the shared memory boolean to false -> prevents the loop from
            repeating -> subprocess terminates when func returns and sleep time interval 
            has passed.
        """
        self.func = func
        self.args = args
        self.interval = interval
        self._run = mp.Value('b', False)
        self.verbose = verbose
        self._proc = None
        self._sigint = sigint
        self._sigterm = sigterm
        self._name = name
        self._auto_kill_on_last_resort = auto_kill_on_last_resort
        self._identifier = None
 
    def __enter__(self):
        return self
    
    def __exit__(self, *exc_args):

        # normal exit        
        if not self._proc.is_alive():
            if self.verbose > 1:
                print("{}loop has stopped".format(self._proc.pid, self._name, t))
            return
        
        
        # loop still runs on context exit -> call stop() -> see what happens
        if self.verbose > 0:
            print("{}loop is still running on context exit".format(self._identifier))
            
        self.stop()
        if self.verbose > 1:
            print("{}give running loop {}s to finish ... ".format(self._identifier, 2*self.interval), end='', flush=True)
        
        self._proc.join(2*self.interval)
        
        if not self._proc.is_alive():
            if self.verbose > 1:
                print("done")
            return

        # loop still runs -> send SIGTERM -> see what happens
        if self.verbose > 1:    
            print("failed!")
        if self.verbose > 0:
            print("{}found running loop still alive -> terminate via SIGTERM ...".format(self._identifier), end='', flush=True)
        
        self._proc.terminate()
        self._proc.join(5*self.interval)
        
        if not self._proc.is_alive():
            if self.verbose > 0:
                print("done!")
                return
            
        if self.verbose > 0:
            print("failed!")

        answer = 'y' if self._auto_kill_on_last_resort else '_'
        while not answer in 'yn':
            print("Do you want to send SIGKILL to '{}'? [y/n]: ".format(self._identifier), end='', flush=True)
            answer = sys.stdin.readline()[:-1]
        if answer == 'n':
            print("{}keeps running".format(self._identifier))
        else:
            print("{}send SIGKILL".format(self._identifier))
            os.kill(self._proc.pid, signal.SIGKILL)

                        

    @staticmethod
    def _wrapper_func(func, args, shared_mem_run, interval, verbose, sigint, sigterm, name):
        """to be executed as a seperate process (that's why this functions is declared static)
        """
        # implement the process specific signal handler
        if name != None:
            identifier = name+': '
        else:
            identifier = "PID {}: ".format(os.getpid())
        
        SIG_handler_Loop(shared_mem_run, sigint, sigterm, verbose)

        
        
        p = psutil.Process() # current process
        
        while shared_mem_run.value:
            try:
                quit_loop = func(*args)
            except:
                err, val, trb = sys.exc_info()
                if verbose > 0:
                    print("\nPID {}({}): error {} occurred in Loop class calling 'func(*args)'".format(p.pid, p.name, err))
                if verbose > 0:
                    traceback.print_tb(trb)
                return

            if quit_loop:
                return
            time.sleep(interval)
            
        if verbose > 1:
            print("PID {}({}): _wrapper_func terminates gracefully".format(p.pid, p.name))

    def start(self):
        """
        uses multiprocess Process to call _wrapper_func in subprocess 
        """

        if self.is_alive():
            if self.verbose > 0:
                print("{}I'm already running".format(self._identifier))
            return
            
        self.run = True
        self._proc = mp.Process(target = Loop._wrapper_func, 
                                args = (self.func, self.args, self._run, self.interval, 
                                        self.verbose, self._sigint, self._sigterm, self._name),
                                name=self._name)
        self._proc.start()
        if self._name != None:
            self._identifier = self._name+': '
        else:
            self._identifier = "PID {}: ".format(self._proc.pid)
        
        if self.verbose > 1:
            print("{}I'm a new loop process".format(self._identifier))
        
    def stop(self):
        """
        stops the process triggered by start
        
        After setting the shared memory boolean run to false, which should prevent
        the loop from repeating, wait at most twice as long as the given
        repetition interval for the _wrapper_function to terminate.
        
        If after that time the _wrapper_function has not terminated, send SIGTERM
        to and the process. Inform the user wether or not the process is yet running.
        """
        self.run = False
        if self._proc == None:
            if self.verbose > 0:
                print("PID None: there is no running loop to stop")
            return
        

            
    def getpid(self):
        """
        return the process id of the spawned process
        """
        if self._proc == None:
            return None

        return self._proc.pid
    
    def is_alive(self):
        if self._proc == None:
            return False
        else:
            return self._proc.is_alive()
        
    
    @property
    def run(self):
        """
        makes the shared memory boolean accessible as class attribute
        
        Set run False, the loop will stop repeating.
        Calling start, will set run True and start the loop again as a new process.
        """
        return self._run.value
    @run.setter
    def run(self, run):
        self._run.value = run

def UnsignedIntValue(val=0):
    return mp.Value('I', val, lock=True)        
       
class StatusBar(Loop):
    """
    status bar in ascii art
    
    Uses Loop class to implement repeating function which shows the process
    based of the two shared memory values max_count and count. max_count is
    assumed to be the final state, whereas count represents the current state.
    
    The estimates time of arrival (ETA) will be calculated from a speed measurement
    given by the average over the last spee_calc_cycles calls of the looping
    function show_stat.
    """
    def __init__(self, 
                 count, 
                 max_count, 
                 interval=1, 
                 speed_calc_cycles=10, 
                 width='auto',
                 run=False, 
                 verbose=0,
                 sigint='stop', 
                 sigterm='stop',
                 name='statusbar'):
        """
        The init will also start to display the status bar if run was set True.
        Otherwise use the inherited method start() to start the show_stat loop.
        stop() will stop to show the status bar.
        
        count [mp.Value] - shared memory to hold the current state
        
        max_count [mp.Value] - shared memory holding the final state, may change
        by external process without having to explicitly tell this class
        
        interval [int] - seconds to wait between progress print
        
        speed_calc_cycles [int] - use the current (time, count) as
        well as the (old_time, old_count) read by the show_stat function 
        speed_calc_cycles calls before to calculate the speed as follows:
        s = count - old_count / (time - old_time)
        
        width [int/'auto'] - the number of characters used to show the status bar,
        use 'auto' to determine width from terminal information -> see __set_width 
        
        verbose, sigint, sigterm -> see loop class  
        """
        
        assert isinstance(count, mp.sharedctypes.Synchronized)
        assert isinstance(max_count, mp.sharedctypes.Synchronized)
        
        self.start_time = mp.Value('d', time.time())
        self.speed_calc_cycles = speed_calc_cycles
        self.q = mp.Queue()         # queue to save the last speed_calc_cycles
                                    # (time, count) information to calculate speed
        self.max_count = max_count  # multiprocessing value type
        self.count = count          # multiprocessing value type
        self.interval = interval
        self.__set_width(width)     # iw
        
        # setup loop class
        super().__init__(func=StatusBar.show_stat, 
                         args=(self.count, self.start_time, self.max_count, 
                               self.width, self.speed_calc_cycles, self.q), 
                         interval=interval, run=run, verbose=verbose, 
                         sigint=sigint, sigterm=sigterm, name=name)
        
    def __set_width(self, width):
        """
        set the number of characters to be used to disply the status bar
        
        is set to 'auto' try to determine the width of the terminal used
        (experimental, depends on the terminal used, os dependens)
        use a width of 80 as fall back.
        """
        if width == 'auto':
            try:
                hw = struct.unpack('hh', fcntl.ioctl(sys.stdin, termios.TIOCGWINSZ, '1234'))
                self.width = hw[1]
            except IOError:
                self.width = 80
        else:
            self.width = width
        
    def set_args(self, interval=1, speed_calc_cycles=10, width='auto'):
        """
        change some of the init arguments
        
        This will stop the loop, set changes and start the loop again.
        """
        self.stop()
        self.interval = interval
        self.speed_calc_cycles = speed_calc_cycles
        self.__set_width(width)
        
        self.args = (self.count, self.start_time, self.max_count, 
                     self.width, self.speed_calc_cycles, self.q)
        self.start()

    @staticmethod        
    def show_stat(count, start_time, max_count, width, speed_calc_cycles, q):
        """
        the actual routine to bring the status to the screen
        """
        count_value = count.value
        max_count_value = max_count.value
        if count_value == 0:
            start_time.value = time.time()
            print("\rwait for first count ...", end='', flush=True)
            return False
        else:
            current_time = time.time()
            start_time_value = start_time.value
            q.put((count_value, current_time))
            
            if q.qsize() > speed_calc_cycles:
                old_count_value, old_time = q.get()
            else:
                old_count_value, old_time = 0, start_time_value

            tet = (current_time - start_time_value)
            speed = (count_value - old_count_value) / (current_time - old_time)
            if speed == 0:
                s3 = "] ETA --"
            else:
                eta = math.ceil((max_count_value - count_value) / speed)
                s3 = "] ETA {}".format(humanize_time(eta))

            
            s1 = "\r{} [{}] [".format(humanize_time(tet), humanize_speed(speed))
            
            l = len(s1) + len(s3)
            l2 = width - l - 1
            
            a = int(l2 * count_value / max_count_value)
            b = l2 - a
            s2 = "="*a + ">" + " "*b
            print(s1+s2+s3, end='', flush=True)
            return count_value >= max_count_value
        
    def stop(self):
        print()
        super().stop()

def setup_SIG_handler_manager():
    """
    When a process calls this functions, it's signal handler
    will be set to ignore the signals given by the list signals.
    
    This functions is passed to the SyncManager start routine (used
    by JobManager_Server) to enable graceful termination when received
    SIGINT or SIGTERM.
    
    The problem is, that the SyncManager start routine triggers a new 
    process to provide shared memory object even over network communication.
    Since the SIGINT signal will be passed to all child processes, the default
    handling would make the SyncManger halt on KeyboardInterrupt Exception.
    
    As we want to shout down the SyncManager at the last stage of cleanup
    we have to prevent this default signal handling by passing this functions
    to the SyncManager start routine.
    """
    Signal_to_SIG_IGN(signals=[signal.SIGINT, signal.SIGTERM], verbose=0)

class Signal_to_SIG_IGN(object):
    def __init__(self, signals=[signal.SIGINT, signal.SIGTERM], verbose=0):
        self.verbose = verbose
        for s in signals:
            signal.signal(s, self._handler)
    def _handler(self, sig, frame):
        if self.verbose > 0:
            print("PID {}: received signal {} -> will be ignored".format(os.getpid(), signal_dict[sig]))


        
class Signal_to_sys_exit(object):
    def __init__(self, signals=[signal.SIGINT, signal.SIGTERM], verbose=0):
        self.verbose = verbose
        for s in signals:
            signal.signal(s, self._handler)
    def _handler(self, signal, frame):
        if self.verbose > 0:
            print("PID {}: received signal {} -> call sys.exit -> raise SystemExit".format(os.getpid(), signal_dict[signal]))
        sys.exit('exit due to signal {}'.format(signal_dict[signal]))
        
class Signal_to_terminate_process_list(object):
    """
    SIGINT and SIGTERM will call terminate for process given in process_list
    """
    def __init__(self, process_list, signals = [signal.SIGINT, signal.SIGTERM], verbose=0):
        self.process_list = process_list
        self.verbose = verbose
        for s in signals:
            signal.signal(s, self._handler)
    def _handler(self, signal, frame):
        if self.verbose > 0:
            print("PID {}: received sig {} -> terminate all given subprocesses".format(os.getpid(), signal_dict[signal]))
        for p in self.process_list:
            p.terminate()

class JobManager_Server(object):
    """general usage:
    
        - init the JobManager_Server, start SyncManager server process
        
        - pass the arguments to be processed to the JobManager_Server
        (put_arg, args_from_list)
        
        - start the JobManager_Server (start), which means to wait for incoming 
        results and to process them. Afterwards process all obtained data.
        
    The default behavior of handling each incoming new result is to simply
    add the pair (arg, result) to the final_result list.
    
    When finished the default final processing is to dump the
    final_result list to fname_for_final_result_dump
    
    To change this behavior you may subclass the JobManager_Server
    and implement
        - an extended __init__ to change the type of the final_result attribute
        - process_new_result
        - process_final_result(self)
        
    In case of any exceptions the JobManager_Server will call process_final_result
    and dump the unprocessed job_q as a list to fname_for_job_q_dump.
    
    Also the signal SIGTERM is caught. In such a case it will raise SystemExit exception
    will will then be handle in the try except clause.
    
    SystemExit and KeyboardInterrupt exceptions are not considered as failure. They are
    rather methods to shout down the Server gracefully. Therefore in such cases no
    traceback will be printed.
    
    All other exceptions are probably due to some failure in function. A traceback
    it printed to stderr.
        
    notes:
        - when the JobManager_Server gets killed (SIGKILL) and the SyncManager still
        lives, the port used will occupied. considere sudo natstat -pna | grep 42524 to
        find the process still using the port
        
        - also the SyncManager ignores SIGTERM and SIGINT signals so you have to send
        a SIGKILL.  
    """
    def __init__(self, 
                  authkey,
                  const_args=None, 
                  port=42524, 
                  verbose=1, 
                  msg_interval=1,
                  fname_for_final_result_dump='auto',
                  fname_for_args_dump='auto',
                  fname_for_fail_dump='auto',
                  no_status_bar = False):
        """
        authkey [string] - authentication key used by the SyncManager. 
        Server and Client must have the same authkey.
        
        const_args [dict] - some constant keyword arguments additionally passed to the
        worker function (see JobManager_Client).
        
        port [int] - network port to use
        
        verbose [int] - 0: quiet, 1: status only, 2: debug messages
        
        msg_interval [int] - number of second for the status bar to update
        
        fname_for_final_result_dump [string/None] - sets the file name used to dump the the
        final_result. (None: do not dump, 'auto' choose filename 
        'YYYY_MM_DD_hh_mm_ss_final_result.dump')
        
        fname_for_args_dump [string/None] - sets the file name used to dump the list
        of unprocessed arguments, if there are any. (None: do not dump at all, 
        'auto' choose filename 'YYYY_MM_DD_hh_mm_ss_args.dump')
        
        fname_for_fail_dump [string/None] - sets the file name used to dump the list 
        of not successfully processed arguments, if there are any. 
        (None: do not dump, 'auto' choose filename 'YYYY_MM_DD_hh_mm_ss_fail.dump')
        
        This init actually starts the SyncManager as a new process. As a next step
        the job_q has to be filled, see put_arg().
        """
        self.verbose = verbose        
        self._pid = os.getpid()
        self._pid_start = None
        if self.verbose > 1:
            print("PID {}: I'm the JobManager_Server main process".format(self._pid))
        
        self.__wait_before_stop = 2
        
        self.port = port
        self.authkey = bytearray(authkey, encoding='utf8')
        self.fname_for_final_result_dump = fname_for_final_result_dump

        self.const_args = copy.copy(const_args)
        self.fname_for_args_dump = fname_for_args_dump
        self.fname_for_fail_dump = fname_for_fail_dump
        
        self.msg_interval = msg_interval

        # to do some redundant checking, might be removed
        # the args_set holds all arguments to be processed
        # in contrast to the job_q, an argument will only be removed
        # from the set if it was caught by the result_q
        # so iff all results have been processed successfully,
        # the args_set will be empty
        self.args_set = set()
        
        # NOTE: it only works using multiprocessing.Queue()
        # the Queue class from the module queue does NOT work  
        self.job_q = mp.Queue()    # queue holding args to process
        self.result_q = mp.Queue() # queue holding returned results
        self.fail_q = mp.Queue()   # queue holding args where processing failed

        class JobQueueManager(SyncManager):
            pass
    
        # make job_q, result_q, fail_q, const_args available via network
        JobQueueManager.register('get_job_q', callable=lambda: self.job_q)
        JobQueueManager.register('get_result_q', callable=lambda: self.result_q)
        JobQueueManager.register('get_fail_q', callable=lambda: self.fail_q)
        JobQueueManager.register('get_const_args', callable=lambda: self.const_args, exposed=["__iter__"])
    
        address=('', self.port)   #ip='' means local
        authkey=self.authkey
    
        self.manager = JobQueueManager(address, authkey)
        
        # start manager with non default signal handling given by
        # the additional init function setup_SIG_handler_manager
        self.manager.start(setup_SIG_handler_manager)
        self.hostname = socket.gethostname()
        
        if self.verbose > 1:
            print("PID {}: SyncManager started {}:{} and authkey '{}'".format(self.manager._process.pid, self.hostname, self.port,  str(authkey, encoding='utf8')))
        
        # thread safe integer values  
        self.numresults = mp.Value('i', 0)  # count the successfully processed jobs
        self.numjobs = mp.Value('i', 0)     # overall number of jobs
        
        # final result as list, other types can be achieved by subclassing 
        self.final_result = []

        self.stat = None
        self.no_status_bar = no_status_bar
        
        self.err_log = []
#         self.p_reinsert_failure = Loop(func=JobManager_Server.reinsert_failures,
#                                            args=(self.fail_q, self.job_q), 
#                                            interval=1, run=False)

    def put_arg(self, a):
        """add argument a to the job_q
        """
        self.args_set.add(a)
        self.job_q.put(copy.copy(a))

        with self.numjobs.get_lock():
            self.numjobs.value += 1
        
    def args_from_list(self, args):
        """serialize a list of arguments to the job_q
        """
        for a in args:
            self.put_arg(a)

    def process_new_result(self, arg, result):
        """Will be called when the result_q has data available.      
        result is the computed result to the argument arg.
        
        Should be overwritten by subclassing!
        """
        self.final_result.append((arg, result))
    
    def process_final_result(self):
        """to implement user defined final processing"""
        pass

    def start(self):
        """
        starts to loop over incoming results
        
        When finished, or on exception call stop() afterwards to shout down gracefully.
        """
        if self._pid != os.getpid():
            raise RuntimeError("do not run JobManager_Server.start() in a subprocess")

        if self.numjobs.value != len(self.args_set):
            raise RuntimeError("use JobManager_Server.put_arg to put arguments to the job_q")
        
        if (self.verbose > 0) and not self.no_status_bar:
            self.stat = StatusBar(count = self.numresults, max_count = self.numjobs, 
                                  interval=self.msg_interval, speed_calc_cycles=10,
                                  verbose = self.verbose, sigint='ign', sigterm='ign',
                                  run=True)        
        
        Signal_to_sys_exit(signals=[signal.SIGTERM, signal.SIGINT], verbose = self.verbose)
        pid = os.getpid()
        
        if self.verbose > 1:
            print("PID {}: JobManager_Server starts processing incoming results".format(pid))
        
        try:   
            if (self.verbose > 0) and not self.no_status_bar:
                self.stat.start()
                if self.verbose > 1:
                    print("PID {}: StatusBar started".format(self.stat.getpid()))
            
            while (len(self.args_set) - self.fail_q.qsize()) > 0:
                try:
                    arg, result = self.result_q.get(timeout=1)       #blocks until an item is available
                    self.args_set.remove(arg)
                    self.process_new_result(arg, result)
                    self.numresults.value = self.numjobs.value - (len(self.args_set) - self.fail_q.qsize()) 
                except queue.Empty:
                    pass

        except:
            if self.verbose > 0:
                err, val, trb = sys.exc_info()
                print("\nPID {}: JobManager_Server caught exception {} -> prepare for graceful shutdown".format(os.getpid(), err))

                # KeyboardInterrupt via SIGINT will be mapped to SystemExit  
                # SystemExit is considered non erroneous
                # halt, printing traceback will be omitted
                if err != SystemExit:
                    print("PID {}: JobManager_Server prints it's traceback")
                    traceback.print_exception(err, val, trb)
        else:    
            if self.verbose > 1:
                print("PID {}: wait {}s before trigger clean up".format(pid, self.__wait_before_stop))
            time.sleep(self.__wait_before_stop)        
        finally:
            self._shoutdown()
            
    def _shoutdown(self):
        """"stop all spawned processes and clean up
        
        - call process_final_result to handle all collected result
        - if job_q is not empty dump remaining job_q
        """
        
        # will only be False when _shutdown was started in subprocess
        
        # start also makes sure that it was not started as subprocess
        # so at default behavior this assertion will allays be True
        assert self._pid == os.getpid()
        
        # stop statusbar 
        if self.stat != None:
            self.stat.stop()
        
        manager_pid = self.manager._process.pid
        
        # stop SyncManager
        self.manager.shutdown()
        
        if self.verbose > 1:
            print("PID {}: SyncManager shutdown ... ".format(manager_pid), end='', flush=True)

        manager_time_out = 2
        # wait at most manager_time_out seconds for the SyncManager subprocess to finish
                    
        self.manager._process.join(manager_time_out)
        
        if self.verbose > 1:
            if self.manager._process.is_alive():
                print("failed (timeout {}s)!".format(manager_time_out))
            else:        
                print("done!")

        # do user defines final processing
        self.process_final_result()
        
        # dump final_result
        if self.fname_for_final_result_dump != None:
            if self.verbose > 0:    
                print("PID {}: dump final_result ... ".format(self._pid), end='', flush=True)
            
            if self.fname_for_final_result_dump == 'auto':
                fname = "{}_final_result.dump".format(getDateForFileName(includePID=False))
            else:
                fname = self.fname_for_final_result_dump

            with open(fname, 'wb') as f:
                pickle.dump(self.final_result, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            if self.verbose > 0:
                print("done!")
        
        # dump not successful or not at all processed arguments as python list 
        if (len(self.args_set) > 0) and (self.fname_for_args_dump != None):
            if self.fname_for_args_dump == 'auto':
                fname = "{}_args.dump".format(getDateForFileName(includePID=False))
            else:
                fname = self.fname_for_args_dump
            
            print("PID {}: args_set not empty -> dump to file '{}' ... ".format(self._pid, fname), end='', flush=True)

            args = list(self.args_set)

            with open(fname, 'wb') as f:
                pickle.dump(args, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            print("done!")

        # dump not successful processed arguments as list with tuple
        # elements of the kind (argument, error name, host name) 
        if not self.fail_q.empty():
            if self.fname_for_fail_dump == 'auto':
                fname = "{}_fail.dump".format(getDateForFileName(includePID=False))
            else:
                fname = self.fname_for_fail_dump
            
            print("PID {}: there are reported failures -> dump to file '{}' ... ".format(self._pid, fname), end='', flush=True)
            
            fail_list = []
            while not self.fail_q.empty():
                new_args, err_name, hostname = self.fail_q.get()
                fail_list.append((new_args, err_name, hostname))
                if self.verbose > 1:
                    print("'{}' on {}:{}".format(err_name, hostname, new_args))
                
            with open(fname, 'wb') as f:
                pickle.dump(fail_list, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            print("done!")
                
        print("PID {}: JobManager_Server was successfully shout down".format(self._pid))


class JobManager_Client(object):
    """
    Calls the functions self.func with arguments fetched from the job_q. You should
    subclass this class and overwrite func to handle your own function.
    
    The job_q is provided by the SycnManager who connects to a SyncManager setup
    by the JobManager_Server. 
    
    Spawns nproc subprocesses (__worker_func) to process arguments. 
    Each subprocess gets an argument from the job_q, processes it 
    and puts the result to the result_q.
    
    If the job_q is empty, terminate the subprocess.
    
    In case of any failure detected within the try except clause
    the argument, which just failed to process, the error and the
    hostname are put to the fail_q so the JobManager_Server can take care of that.
    
    After that the traceback is written to a file with name 
    traceback_args_<args>_err_<err>_<YYYY>_<MM>_<DD>_<hh>_<mm>_<ss>_<PID>.trb.
    
    Then the process will terminate.
    """
    
    def __init__(self, ip, authkey, port = 42524, nproc = 0, nice=19, no_warings=False, verbose=1):
        """
        ip [string] - ip address or hostname where the JobManager_Server is running
        
        authkey [string] - authentication key used by the SyncManager. 
        Server and Client must have the same authkey.
        
        port [int] - network port to use
        
        nproc [integer] - number of subprocesses to start
            
            positive integer: number of processes to spawn
            
            zero: number of spawned processes == number cpu cores
            
            negative integer: number of spawned processes == number cpu cores - |nproc|
        
        nice [integer] - niceness of the subprocesses
        
        no_warnings [bool] - call warnings.filterwarnings("ignore") -> all warnings are ignored
        
        verbose [int] - 0: quiet, 1: status only, 2: debug messages
        """
        
       
        self.verbose = verbose
        self._pid = os.getpid()
        if self.verbose > 1:
            print("PID {}: I'm the JobManager_Client main process".format(self._pid))
       
        if no_warings:
            import warnings
            warnings.filterwarnings("ignore")
        self.ip = ip
        self.authkey = bytearray(authkey, encoding='utf8')
        self.port = port
        self.nice = nice
        if nproc > 0:
            self.nproc = nproc
        else:
            self.nproc = mp.cpu_count() + nproc
            assert self.nproc > 0

        self.procs = []
        
       
    @staticmethod
    def _get_manager_object(ip, port, authkey, verbose=0):
        """
        connects to the server and get registered shared objects such as
        job_q, result_q, fail_q, const_args
        """
        class ServerQueueManager(SyncManager):
            pass
        
        ServerQueueManager.register('get_job_q')
        ServerQueueManager.register('get_result_q')
        ServerQueueManager.register('get_fail_q')
        ServerQueueManager.register('get_const_args', exposed="__iter__")
    
        manager = ServerQueueManager(address=(ip, port), authkey=authkey)

        if verbose > 0:
            print('PIC {}: connecting to {}:{} ... '.format(os.getpid(), ip, port), end='', flush=True)
        try:
            manager.connect()
        except:
            if verbose > 0:    
                print('failed!')
            
            err, val, trb = sys.exc_info()
            print("caught exception {}: {}".format(err.__name__, val))
            
            if verbose > 1:
                traceback.print_exception(err, val, trb)
            return None
        else:
            if verbose > 0:    
                print('done!')
            
        
        job_q = manager.get_job_q()
        result_q = manager.get_result_q()
        fail_q = manager.get_fail_q()
        const_args = manager.get_const_args()
        return job_q, result_q, fail_q, const_args
        
    @staticmethod
    def func(args, const_args):
        """
        function to be called by the worker processes
        
        args - provided by the job_q of the JobManager_Server
        
        const_args - tuple of const args also provided by the JobManager_Server
        
        NOTE: This is just some dummy implementation to be used for test reasons only!
        Subclass and overwrite this function to implement your own function.  
        """
        time.sleep(0.1)
        return os.getpid()


    @staticmethod
    def __worker_func(func, nice, verbose, ip, port, authkey):
        """
        the wrapper spawned nproc trimes calling and handling self.func
        """
        Signal_to_sys_exit(signals=[signal.SIGTERM, signal.SIGINT])
        
        res = JobManager_Client._get_manager_object(ip, port, authkey, verbose)
        if res == None:
            if verbose > 1:
                print("PID {}: no shared object recieved, terminate!".format(os.getpid()))
            sys.exit(1)
        else:
            job_q, result_q, fail_q, const_args = res 
        
        n = os.nice(0)
        n = os.nice(nice - n)
        c = 0
        if verbose > 1:
            print("PID {}: now alive, niceness {}".format(os.getpid(), n))
        while True:
            try:
                new_args = job_q.get(block = True, timeout = 0.1)
                res = func(new_args, const_args)
                result_q.put((new_args, res))
                c += 1
                
            # regular case, just stop woring when empty job_q was found
            except queue.Empty:
                if verbose > 0:
                    print("\nPID {}: finds empty job queue, processed {} jobs".format(os.getpid(), c))
                return
            
            # in this context usually raised if the communication to the server fails
            except EOFError:
                if verbose > 0:
                    print("\nPID {}: EOFError, I guess the server went down, can't do anything, terminate now!".format(os.getpid()))
                return
            
            # considered as normal exit caused by some user interaction, SIGINT, SIGTERM
            # note SIGINT, SIGTERM -> SystemExit is achieved by overwriting the
            # default signal handlers
            except SystemExit:
                if verbose > 0:
                    print("\nPID {}: SystemExit, quit processing, reinsert current argument".format(os.getpid()))
                try:
                    if verbose > 1:
                        print("\PID {}: put argument back to job_q ... ".format(os.getpid()), end='', flush=True)
                    job_q.put(new_args, timeout=10)
                except queue.Full:
                    if verbose > 0:
                        print("\nPID {}: failed to reinsert argument, Server down? I quit!".format(os.getpid()))
                else:
                    if verbose > 1:
                        print("done!")
                return
            
            # some unexpected Exception
            # write argument, exception name and hostname to fail_q, save traceback
            # continue workung 
            except:
                err, val, trb = sys.exc_info()
                if verbose > 0:
                    print("\nPID {}: caught exception '{}', report failure of current argument to server ... ".format(os.getpid(), err.__name__), end='', flush=True)
                
                hostname = socket.gethostname()
                fname = 'traceback_err_{}_{}.trb'.format(err.__name__, getDateForFileName(includePID=True))
                fail_q.put((new_args, err.__name__, hostname), timeout=10)
                
                if verbose > 0:
                    print("done")
                    print("        write exception to file {} ... ".format(fname), end='', flush=True)

                with open(fname, 'w') as f:
                    traceback.print_exception(etype=err, value=val, tb=trb, file=f)
                                    
                if verbose > 0:
                    print("done")
                    print("        continue processing next argument.")
                
        if verbose > 1:
            print("PID {}: JobManager_Client.__worker_func terminates".format(os.getpid()))
        

    def start(self):
        """
        starts a number of nproc subprocess to work on the job_q
        
        SIGTERM and SIGINT are managed to terminate all subprocesses
        
        retruns when all subprocesses have terminated
        """
        if self.verbose > 1:
            print("PID {}: start {} processes to work on the remote queue".format(os.getpid(), self.nproc))
        for i in range(self.nproc):
            p = mp.Process(target=self.__worker_func, args=(self.func, 
                                                            self.nice, 
                                                            self.verbose, 
                                                            self.ip, 
                                                            self.port,
                                                            self.authkey))
            self.procs.append(p)
            p.start()
            time.sleep(0.3)
        
        Signal_to_terminate_process_list(process_list = self.procs, verbose=self.verbose)
    
        for p in self.procs:
            p.join()
                       
        