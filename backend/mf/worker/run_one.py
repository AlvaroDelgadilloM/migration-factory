"""Entry point of an ephemeral build pod (MF_EXECUTION_MODE=k8s): run one platform job and exit."""
import logging
import sys

from . import tasks
from .main import harden_process


def main(job_id: str) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    harden_process()
    tasks.run_job(job_id)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
