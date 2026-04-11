import os
import shutil

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def delete_all_logs(log_dir=LOG_DIR):
    """Delete all files and folders under the logs directory."""
    if not os.path.exists(log_dir):
        print(f"No log directory found at: {log_dir}")
        return

    if not os.path.isdir(log_dir):
        raise RuntimeError(f"Log path exists but is not a directory: {log_dir}")

    for entry in os.listdir(log_dir):
        path = os.path.join(log_dir, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    print(f"Deleted all log contents from: {log_dir}")


if __name__ == "__main__":
    delete_all_logs()
