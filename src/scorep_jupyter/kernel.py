from ipykernel.kernelbase import Kernel
from subprocess import PIPE
import os
import scorep_jupyter.userpersistence as userpersistence
import uuid
import signal
import subprocess
import sys
import shutil
import logging

PYTHON_EXECUTABLE = sys.executable
userpersistence_token = "scorep_jupyter.userpersistence"


# Exception to throw when reading data from stdout/stderr of subprocess
class MyTimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise MyTimeoutException


signal.signal(signal.SIGALRM, timeout_handler)
signal_timeout = 0.1


class ScorepPythonKernel(Kernel):
    implementation = 'Python and ScoreP'
    implementation_version = '1.0'
    language = 'python'
    language_version = '3.8'
    language_info = {
        'name': 'Any text',
        'mimetype': 'text/plain',
        'file_extension': '.py',
    }
    banner = "A python kernel with scorep binding"

    userEnv = {}
    scorePEnv = {}
    scoreP_python_args = False
    multicellmode = False
    init_multicell = False
    multicellmode_cellcount = 0
    tmpUserPers = ""
    tmpUserVars = ""
    tmpDir = ""
    tmpCodeString = ""

    def __init__(self, **kwargs):
        Kernel.__init__(self, **kwargs)
        uid = str(uuid.uuid4())
        self.tmpDir = "tmp" + uid + "/"
        os.mkdir(self.tmpDir)
        sys.path.append(os.path.realpath(self.tmpDir))

        #logging.basicConfig(filename="kernel_log.log", level=logging.DEBUG)

        self.tmpUserPers = self.tmpDir + "tmpUserPers" + uid + ".py"
        self.tmpUserVars = self.tmpDir + ".tmpUserVars" + uid
        self.userEnv["PYTHONUNBUFFERED"] = "x"

        # needed for subprocesses
        self.userEnv["PYTHONPATH"] = ":".join(sys.path)

    def get_output_and_print(self, process2observe):
        while True:
            err = b''
            output = b''
            signal.setitimer(signal.ITIMER_REAL, signal_timeout)
            try:
                while True:
                    err += process2observe.stderr.readline(1)
            except MyTimeoutException:
                pass

            signal.setitimer(signal.ITIMER_REAL, signal_timeout)
            try:
                while True:
                    output += process2observe.stdout.readline(1)
            except MyTimeoutException:
                pass
            if process2observe.poll() is not None and output == b'' and err == b'':
                break
            if output:
                # ignore errors in encoding here, since that is not our responsibility (but the one of the user's code)
                stream_content_stdout = {'name': 'stdout',
                                         'text': output.decode(sys.getdefaultencoding(), errors='ignore')}
                self.send_response(self.iopub_socket, 'stream', stream_content_stdout)
            if err:
                stream_content_stderr = {'name': 'stderr',
                                         'text': err.decode(sys.getdefaultencoding(), errors='ignore')}
                self.send_response(self.iopub_socket, 'stream', stream_content_stderr)

    def set_scorep_env(self, code):
        # set scorep environment variables
        scorep_vars = code.split('\n')
        # iterate from first line since this is scoreP_env indicator
        for var in scorep_vars[1:]:
            key_val = var.split('=')
            self.scorePEnv[key_val[0]] = key_val[1]
        stream_content_stdout = {'name': 'stdout',
                                 'text': 'set score-p environment successfully: ' + str(self.scorePEnv)}
        self.userEnv.update(self.scorePEnv)
        self.send_response(self.iopub_socket, 'stream', stream_content_stdout)

    def set_scorep_pythonargs(self, code):
        # set scorep python bindings arguments
        self.scoreP_python_args = ' '.join(code.split('\n')[1:])
        stream_content_stdout = {'name': 'stdout',
                                 'text': 'use the following score-p python binding arguments: ' + str(
                                     self.scoreP_python_args)}
        self.send_response(self.iopub_socket, 'stream', stream_content_stdout)
        # elif code.startswith('%%profile'):
        # start with internal profiling mode
        # self.manage_profiling_commands(code)

    def enable_multicellmode(self):
        # start to mark the cells for multi cell mode
        self.tmpCodeString = ""
        self.multicellmode = True
        self.multicellmode_cellcount = 0
        self.init_multicell = True
        stream_content_stdout = {'name': 'stdout',
                                 'text': 'started multi-cell mode. The following cells will be marked.'}
        self.send_response(self.iopub_socket, 'stream', stream_content_stdout)

    def abort_multicellmode(self):
        # abort the multi cell mode
        self.multicellmode = False
        self.init_multicell = False
        self.multicellmode_cellcount = 0
        stream_content_stdout = {'name': 'stdout', 'text': 'aborted multi-cell mode.'}
        self.send_response(self.iopub_socket, 'stream', stream_content_stdout)

    def prepare_code(self, codeStr, codeWithUserVars):

        user_variables = userpersistence.get_user_variables_from_code(codeWithUserVars)
        # all cells that are not executed in multi cell mode have to import them
        codeStr += "import " + userpersistence_token + "\n"
        # prior imports can be loaded before runtime. we have to load them this way because they can not be
        # pickled

        # user variables can be pickled and should be loaded at runtime
        if os.path.isfile(self.tmpUserPers):
            with open(self.tmpUserPers, "r") as f:
                prev_userpersistence = f.read()
                codeStr += prev_userpersistence + "\n"
        codeStr += "globals().update(" + userpersistence_token + ".load_user_variables('" + self.tmpUserVars + "'))\n"

        if not self.multicellmode:
            codeStr += "\n" + codeWithUserVars

        codeStr += "\n" + userpersistence_token + ".save_user_variables(globals(), " + str(user_variables) + ", '" + \
                   self.tmpUserPers + "', '" + self.tmpUserVars + "')"

        userpersistence.save_user_definitions(codeWithUserVars, self.tmpUserPers)
        return codeStr

    def finalize_multicellmode(self, silent):
        # finish multi cell mode and execute the code of the marked cells
        stream_content_stdout = {'name': 'stdout', 'text': 'finalizing multi-cell mode and execute cells.'}
        self.send_response(self.iopub_socket, 'stream', stream_content_stdout)

        self.tmpCodeString = self.prepare_code(self.tmpCodeString, self.tmpCodeString)

        self.multicellmode = False
        self.init_multicell = False
        self.multicellmode_cellcount = 0

        self.execute_code(execute_with_scorep=True, silent=silent)

    def execute_code(self, execute_with_scorep, silent):
        # execute cell with or without scorep

        #logging.info(str(self.tmpCodeString))
        if execute_with_scorep:
            if self.scoreP_python_args:
                user_code_process = subprocess.Popen(
                    [PYTHON_EXECUTABLE, "-m", "scorep", self.scoreP_python_args, '-c', self.tmpCodeString], stdout=PIPE,
                    stderr=PIPE, env=os.environ.update(self.userEnv))
            else:
                user_code_process = subprocess.Popen(
                    [PYTHON_EXECUTABLE, "-m", "scorep", '-c', self.tmpCodeString], stdout=PIPE, stderr=PIPE,
                    env=os.environ.update(self.userEnv))

        else:
            user_code_process = subprocess.Popen([PYTHON_EXECUTABLE, '-c', self.tmpCodeString], stdout=PIPE,
                                                 stderr=PIPE, env=os.environ.update(self.userEnv))

        if not silent:
            self.get_output_and_print(user_code_process)

    # overwrite the default do_execute() function of the ipykernel
    def do_execute(self, code, silent, store_history=True, user_expressions=None,
                   allow_stdin=False):

        if code.startswith('%%scorep_env'):
            self.set_scorep_env(code)
        elif code.startswith('%%scorep_python_binding_arguments'):
            self.set_scorep_pythonargs(code)
        elif code.startswith('%%enable_multicellmode'):
            self.enable_multicellmode()
        elif code.startswith('%%abort_multicellmode'):
            self.abort_multicellmode()
        elif code.startswith('%%finalize_multicellmode'):
            self.finalize_multicellmode(silent)

        else:
            execute_with_scorep = code.startswith('%%execute_with_scorep')
            if execute_with_scorep:
                # just remove first line (execute_with_scorep indicator)
                code = code.split("\n", 1)[1]
            if self.multicellmode:
                # in multi cell mode, just append the code because we execute multiple cells as one
                self.multicellmode_cellcount += 1
                self.tmpCodeString += "\n" + code
                stream_content_stdout = {'name': 'stdout',
                                         'text': 'marked the cell for multi-cell mode. This cell will be executed at '
                                                 'position: ' + str(self.multicellmode_cellcount)}
                self.send_response(self.iopub_socket, 'stream', stream_content_stdout)
            else:
                self.tmpCodeString = self.prepare_code("", code)
                self.execute_code(execute_with_scorep, silent)

            # add original cell code

            # for persistence reasons, we have to store user defined variables after the execution of the cell.
            # therefore we use shelve/pickle for object serialization and object persistence imports, user defined
            # classes and methods are handled by storing them in code files and prepending them to the execution cell
            # code However, if the user defines some network connections, filereaders etc. this might fail due to
            # pickle limitations

        return {'status': 'ok',
                # The base class increments the execution count
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {},
                }

    def do_shutdown(self, restart):
        if os.path.exists(self.tmpDir):
            shutil.rmtree(self.tmpDir)

        return {'status': 'ok',
                'restart': restart
                }

    def do_clear(self):
        pass

    def do_apply(self, content, bufs, msg_id, reply_metadata):
        pass

    '''
    def manage_profiling_commands(self, code):
        if code.startswith('%%profile_runtime'):
            prof = cubex.open('profile.cubex')
            metrics = prof.show_metrics()
            stream_content_stdout = {'data': metrics, 'metadata': metrics}
        elif code.startswith('%%profile_memory'):
            stream_content_stdout = {'name': 'stdout', 'text': 'memory'}
        elif code.startswith('%%profile_functioncalls'):
            stream_content_stdout = {'name': 'stdout', 'text': 'calls'}

        self.send_response(self.iopub_socket, 'display_data', stream_content_stdout)
    '''


if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp

    IPKernelApp.launch_instance(kernel_class=ScorepPythonKernel)
