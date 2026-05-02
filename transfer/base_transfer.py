"""Defines the abstract base class for transfer operations."""

from abc import ABC, abstractmethod


class TransferHandler(ABC):
    """Abstract base class for handling file transfers and remote listing."""

    @abstractmethod
    def list_remote_files(self) -> tuple[list[tuple[str, int]], set[str], set[str]]:
        """Lists files in the remote directory.

        Returns:
            Tuple containing:
            - List of (.zst filename, size_in_bytes) tuples.
            - Set of .parquet filenames.
            - Set of other filenames (e.g., .manifest.json, .claim.json).
        """
        pass

    @abstractmethod
    def download_file(self, remote_filename: str, local_path: str, expected_size: int) -> tuple[bool, float]:
        """Downloads a single file from the remote host.

        Args:
            remote_filename: The name of the file on the remote host.
            local_path: The full local path to save the file to.
            expected_size: The expected size of the file for verification.

        Returns:
            Tuple: (success: bool, elapsed_time: float)
        """
        pass

    @abstractmethod
    def upload_file(self, local_path: str, remote_filename: str) -> tuple[bool, float]:
        """Uploads a single file to the remote host.

        Args:
            local_path: The full local path of the file to upload.
            remote_filename: The destination filename on the remote host.

        Returns:
            Tuple: (success: bool, elapsed_time: float)
        """
        pass

    @abstractmethod
    def delete_file(self, remote_filename: str) -> bool:
        """Deletes a file from the remote host."""
        pass

    @abstractmethod
    def download_to_string(self, remote_filename: str) -> str:
        """Downloads a remote file and returns its content as a string."""
        pass

    @abstractmethod
    def file_exists(self, remote_filename: str) -> bool:
        """Checks if a file exists on the remote host."""
        pass

    @abstractmethod
    def check_prerequisites(self) -> bool:
        """Checks if the required command-line tools for this transfer method are available."""
        pass

    @abstractmethod
    def check_connection(self) -> bool:
        """Checks if the connection to the remote host can be established."""
        pass

    def close(self):
        """Closes any persistent connections (optional)."""
        pass
