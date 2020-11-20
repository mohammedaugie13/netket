import json as _json
from os import path as _path
from netket.vmc_common import tree_map as _tree_map
import numpy as _np

import tarfile
from io import BytesIO


def _exists_json(prefix):
    return _path.exists(prefix + ".log") or _path.exists(prefix + ".wf")


def _to_json(ob):
    if hasattr(ob, "to_json"):
        return ob.to_json()
    # array-like (for example CUDA or Jax arrays) satisfy the __array__
    # protocol.
    # https://numpy.org/doc/stable/reference/generated/numpy.array.html
    elif hasattr(ob, "__array__"):
        # return _np.array(ob)
        return None
    else:
        return ob


def numpy_to_buffer(arr):
    buf = BytesIO()
    np.save(buf, arr, allow_pickle=False)
    buf.seek(0)
    return buf


def save_numpy_to_tar(tar_file, data, name):
    # convert data to a buffer in memory
    abuf = numpy_to_buffer(data)
    # Contruct the info object with the correct length
    info = tarfile.TarInfo(name=name)
    info.size = len(abuf.getbuffer())

    # actually save the data to the tar file
    tar_file.addfile(tarinfo=info, fileobj=abuf)


def save_binary_to_tar(tar_file, byte_data, name):
    abuf = BytesIO(byte_data)

    # Contruct the info object with the correct length
    info = tarfile.TarInfo(name=name)
    info.size = len(abuf.getbuffer())

    # actually save the data to the tar file
    tar_file.addfile(tarinfo=info, fileobj=abuf)


class JsonLog:
    """
    Creates a Json Logger sink object, that can be passed with keyword argument `logger` to Monte
    Carlo drivers in order to serialize the outpit data of the simulation.

    Args:
        output_prefix: the name of the output files before the extension
        save_params_every: every how many iterations should machine parameters be flushed to file
        write_every: every how many iterations should data be flushed to file
        mode: Specify the behaviour in case the file already exists at this output_prefix. Options
        are
        - `[w]rite`: (default) overwrites file if it already exists;
        - `[a]ppend`: appends to the file if it exists, overwise creates a new file;
        - `[x]` or `fail`: fails if file already exists;
    """

    def __init__(
        self,
        output_prefix,
        mode="write",
        save_params_every=50,
        write_every=50,
        tar_parameters=False,
    ):
        # Shorthands for mode
        if mode == "w":
            mode = "write"
        elif mode == "a":
            mode = "append"
        elif mode == "x":
            mode = "fail"

        if not ((mode == "write") or (mode == "append") or (mode == "fail")):
            raise ValueError(
                "Mode not recognized: should be one of `[w]rite`, `[a]ppend` or `[x]`(fail)."
            )

        file_exists = _exists_json(output_prefix)

        starting_json_content = {"Output": []}
        starting_step = 0

        if file_exists and mode == "append":
            # if there is only the .wf file but not the json one, raise an error
            if not _path.exists(output_prefix + ".log"):
                raise ValueError(
                    "History file does not exists, but wavefunction file does. Please change `output_prefix or set mode=`write`."
                )

            starting_json_content = _json.load(open(output_prefix + ".log"))
            starting_step = starting_json_content["Output"]["Iteration"][-1]

        elif file_exists and mode == "fail":
            raise ValueError(
                "Output file already exists. Either delete it manually or change `output_prefix`."
            )

        self._json_out = starting_json_content
        self._prefix = output_prefix
        self._write_every = write_every
        self._save_params_every = save_params_every
        self._old_step = starting_step
        self._step_shift = 0
        self._steps_notflushed_write = 0
        self._steps_notflushed_pars = 0
        self._files_open = [output_prefix + ".log", output_prefix + ".wf"]
        self._tar_params = False

        if tar_parameters:
            if mode == "write":
                mode = "w"
            elif mode == "append":
                mode = "a"
            self._tar_params = True
            self._tar_file_created = False
            self._tar_file_mode = mode

    def previous_step(self):
        return self._old_step + self._step_shift

    def _create_tar_file(self):
        self._tar_file = tarfile.TarFile(self._prefix + ".tar", self._tar_file_mode)
        self._files_open.append(self._prefix + ".tar")
        self._tar_file_created = True

    def __call__(self, step, item, machine):
        if step + self._step_shift <= self._old_step:
            self._step_shift += self.previous_step() - step

        item["Iteration"] = step + self._step_shift

        self._json_out["Output"].append(item)

        if self._tar_params and machine is not None:
            if not self._tar_file_created:
                self._create_tar_file()

            save_binary_to_tar(
                self._tar_file, machine.to_bytes(), str(item["Iteration"])
            )

        if (
            self._steps_notflushed_write % self._write_every == 0
            or step == self._old_step - 1
        ):
            self._flush_log()
        if (
            self._steps_notflushed_pars % self._save_params_every == 0
            or step == self._old_step - 1
        ):
            self._flush_params(machine)

        self._old_step = step
        self._steps_notflushed_write += 1
        self._steps_notflushed_pars += 1

    def _flush_log(self):
        with open(self._prefix + ".log", "w") as outfile:
            log_data = _tree_map(_to_json, self._json_out)
            _json.dump(log_data, outfile)
            self._steps_notflushed_write = 0

    def _flush_params(self, machine):
        machine.save(self._prefix + ".wf")
        self._steps_notflushed_pars = 0

    def flush(self, machine=None):
        """
        Writes to file the content of this logger.

        :param machine: optionally also writes the parameters of the machine.
        """
        self._flush_log()

        if machine is not None:
            self._flush_params(machine)
