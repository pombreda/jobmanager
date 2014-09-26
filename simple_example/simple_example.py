import numpy as np
import multiprocessing as mp
import time

import sys
sys.path.append('../')
import jobmanager as jm

class Example_Client(jm.JobManager_Client):
    def __init__(self):
        # start quiet client (verbopse=0)
        super().__init__(server="localhost", 
                         authkey='simple example', 
                         verbose=0)
        
    @staticmethod
    def func(args, const_args):
        """simply return the current argument"""
        return args

        

class Example_Server(jm.JobManager_Server):
    def __init__(self):
        # server show status information (verbose=1)
        super().__init__(authkey='simple example',
                         verbose=2)

        self.final_result = 1
            
        
    def process_new_result(self, arg, result):
        """over write final_result with the new incoming result 
        if the new result is smaller then the final_result""" 
        if self.final_result > result:
            self.final_result = result
            
    def process_final_result(self):
        print("final_result:", self.final_result)
        

def run_server():
    server = Example_Server()
    for i in range(10000):
        server.put_arg(np.random.rand())
        
    server.start()
    
def run_client():
    client = Example_Client()
    client.start()

if __name__ == "__main__":
    p_server = mp.Process(target=run_server)
    p_server.start()
    
    time.sleep(1)
    
    p_client = mp.Process(target=run_client)
    p_client.start()

    p_client.join()
    p_server.join()