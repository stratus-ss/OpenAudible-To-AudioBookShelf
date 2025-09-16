"""
Integration tests for AudioBookshelf using real server and data
"""
import os
import pytest
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

from modules.audio_bookshelf import (
    get_all_books,
    scan_library_for_books,
    get_audio_bookshelf_recent_books,
    process_audio_books,
)
from tests.test_config import (
    REAL_ABS_SERVER_URL, 
    REAL_ABS_API_TOKEN, 
    OPENAUDIBLE_BOOKS_DIR,
    ABS_MOUNT_DIR
)


def get_library_id(server_url, api_token):
    """Get the first available library ID from the server"""
    if not api_token:
        return None
    
    response = requests.get(
        f"{server_url}/api/libraries",
        headers={"Authorization": f"Bearer {api_token}"}
    )
    
    if response.status_code == 200:
        libraries = response.json()["libraries"]
        if libraries:
            return libraries[0]["id"]
    return None


class TestBasicAudioBookshelfConnectivity:
    """Basic tests that don't require libraries to be set up"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup for basic connectivity tests"""
        if not REAL_ABS_API_TOKEN:
            pytest.skip("No API token available - create sample_files/abs_api.key.example with your token")
        
        self.server_url = REAL_ABS_SERVER_URL
        self.api_token = REAL_ABS_API_TOKEN
    
    def test_server_connectivity(self):
        """Test basic connectivity to the AudioBookshelf server"""
        response = requests.get(f"{self.server_url}/ping")
        assert response.status_code == 200, f"Server {self.server_url} is not accessible"
        print(f"✅ AudioBookshelf server is accessible at {self.server_url}")
    
    def test_api_authentication(self):
        """Test API authentication with the real server"""
        response = requests.get(
            f"{self.server_url}/api/me",
            headers={"Authorization": f"Bearer {self.api_token}"}
        )
        assert response.status_code == 200, f"API authentication failed: {response.text}"
        user_data = response.json()
        assert "username" in user_data, "Expected user data not found"
        print(f"✅ API authentication successful for user: {user_data.get('username', 'Unknown')}")
    
    def test_check_libraries_setup(self):
        """Test getting libraries and provide setup guidance if none found"""
        response = requests.get(
            f"{self.server_url}/api/libraries",
            headers={"Authorization": f"Bearer {self.api_token}"}
        )
        assert response.status_code == 200, "Failed to get libraries"
        
        data = response.json()
        assert "libraries" in data, "Libraries data not found"
        
        libraries_count = len(data["libraries"])
        print(f"📚 Found {libraries_count} libraries on AudioBookshelf server")
        
        if libraries_count == 0:
            print("📝 To set up your first library:")
            print("   1. Log into AudioBookshelf at https://audiobookshelf.x86experts.com")
            print("   2. Go to Settings > Libraries") 
            print("   3. Add a library pointing to /mnt/audiobooks")
            print("   4. After adding a library, the remaining integration tests will run")


class TestRealAudioBookshelfIntegration:
    """Integration tests using real AudioBookshelf server and data"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup for integration tests - skip if no API token available"""
        if not REAL_ABS_API_TOKEN:
            pytest.skip("No API token available - create sample_files/abs_api.key")
        
        self.server_url = REAL_ABS_SERVER_URL
        self.api_token = REAL_ABS_API_TOKEN
        self.library_id = get_library_id(self.server_url, self.api_token)
        
        if not self.library_id:
            pytest.skip("No libraries found on AudioBookshelf server. Please create a library first:\n"
                       "1. Log into AudioBookshelf at https://audiobookshelf.x86experts.com\n"
                       "2. Go to Settings > Libraries\n"
                       "3. Add a library pointing to /mnt/audiobooks")
    
    def test_server_connectivity(self):
        """Test basic connectivity to the AudioBookshelf server"""
        response = requests.get(f"{self.server_url}/ping")
        assert response.status_code == 200, f"Server {self.server_url} is not accessible"
    
    def test_api_authentication(self):
        """Test API authentication with the real server"""
        response = requests.get(
            f"{self.server_url}/api/me",
            headers={"Authorization": f"Bearer {self.api_token}"}
        )
        assert response.status_code == 200, "API authentication failed"
        user_data = response.json()
        assert "username" in user_data, "Expected user data not found"
    
    def test_get_libraries(self):
        """Test getting libraries from the real server"""
        response = requests.get(
            f"{self.server_url}/api/libraries",
            headers={"Authorization": f"Bearer {self.api_token}"}
        )
        assert response.status_code == 200, "Failed to get libraries"
        
        data = response.json()
        assert "libraries" in data, "Libraries data not found"
        assert len(data["libraries"]) > 0, "No libraries found"
    
    def test_get_all_books_real(self):
        """Test get_all_books with real AudioBookshelf server"""
        response = get_all_books(
            self.server_url, 
            self.library_id, 
            self.api_token
        )
        
        assert response.status_code == 200, f"Failed to get books: {response.text}"
        
        data = response.json()
        assert "results" in data, "Expected 'results' key in response"
        
        # Verify the structure of returned books
        if data["results"]:
            book = data["results"][0]
            assert "id" in book, "Book missing 'id' field"
            assert "media" in book, "Book missing 'media' field"
            assert "metadata" in book["media"], "Book missing metadata"
    
    def test_scan_library_real(self):
        """Test library scanning with real server"""
        response = scan_library_for_books(
            self.server_url,
            self.library_id,
            self.api_token
        )
        
        # Scan can return 200 (started) or other codes depending on server state
        assert response.status_code in [200, 409], f"Unexpected scan response: {response.status_code}"
    
    def test_get_audio_bookshelf_recent_books_real(self):
        """Test getting recent books with real data"""
        # First get all books
        all_books_response = get_all_books(
            self.server_url,
            self.library_id, 
            self.api_token
        )
        assert all_books_response.status_code == 200
        
        # Test getting books from last 30 days
        recent_books = get_audio_bookshelf_recent_books(
            all_books_response,
            days_ago=30
        )
        
        assert isinstance(recent_books, list), "Expected list of recent books"
        
        # Verify structure if any books found
        if recent_books:
            book = recent_books[0]
            assert "addedAt" in book, "Recent book missing addedAt timestamp"
            assert "media" in book, "Recent book missing media data"


class TestOpenAudibleBooksDirectory:
    """Tests for OpenAudible books directory validation"""
    
    def test_openaudible_directory_exists(self):
        """Test that OpenAudible books directory exists"""
        books_path = Path(OPENAUDIBLE_BOOKS_DIR)
        assert books_path.exists(), f"OpenAudible books directory not found: {OPENAUDIBLE_BOOKS_DIR}"
        assert books_path.is_dir(), f"Path is not a directory: {OPENAUDIBLE_BOOKS_DIR}"
    
    def test_openaudible_has_books(self):
        """Test that OpenAudible directory contains audio book files"""
        books_path = Path(OPENAUDIBLE_BOOKS_DIR)
        if not books_path.exists():
            pytest.skip(f"OpenAudible books directory not found: {OPENAUDIBLE_BOOKS_DIR}")
        
        # Look for common audio book file extensions
        audio_extensions = ['.m4b', '.mp3', '.m4a', '.aac']
        audio_files = []
        
        for ext in audio_extensions:
            audio_files.extend(list(books_path.glob(f"*{ext}")))
        
        assert len(audio_files) > 0, f"No audio book files found in {OPENAUDIBLE_BOOKS_DIR}"
        
        # Verify file sizes are reasonable (not empty)
        for audio_file in audio_files[:5]:  # Check first 5 files
            assert audio_file.stat().st_size > 0, f"Audio file appears empty: {audio_file}"
    
    def test_parse_openaudible_filenames(self):
        """Test parsing of OpenAudible filename patterns"""
        books_path = Path(OPENAUDIBLE_BOOKS_DIR)
        if not books_path.exists():
            pytest.skip(f"OpenAudible books directory not found: {OPENAUDIBLE_BOOKS_DIR}")
        
        m4b_files = list(books_path.glob("*.m4b"))
        if not m4b_files:
            pytest.skip("No .m4b files found for filename parsing test")
        
        # Test that we can extract meaningful info from filenames
        parsed_books = []
        for book_file in m4b_files:
            # Basic filename parsing - titles should be extractable
            title = book_file.stem
            assert len(title) > 0, f"Could not extract title from: {book_file.name}"
            
            parsed_books.append({
                "filename": book_file.name,
                "title": title,
                "size": book_file.stat().st_size
            })
        
        assert len(parsed_books) > 0, "No books successfully parsed"


class TestAudioBookshelfMountDirectory:
    """Tests for AudioBookshelf mount directory"""
    
    def test_mount_directory_exists(self):
        """Test that the AudioBookshelf mount directory exists"""
        mount_path = Path(ABS_MOUNT_DIR)
        # This might be empty or not exist if not properly mounted
        if not mount_path.exists():
            pytest.skip(f"AudioBookshelf mount directory not found: {ABS_MOUNT_DIR}")
        
        assert mount_path.is_dir(), f"Mount path is not a directory: {ABS_MOUNT_DIR}"
    
    def test_mount_directory_writable(self):
        """Test that the mount directory is writable (for book transfers)"""
        mount_path = Path(ABS_MOUNT_DIR)
        if not mount_path.exists():
            pytest.skip(f"AudioBookshelf mount directory not found: {ABS_MOUNT_DIR}")
        
        # Test write permission with a temporary file
        test_file = mount_path / ".test_write_permission"
        try:
            test_file.write_text("test")
            test_file.unlink()  # Clean up
        except (PermissionError, OSError) as e:
            pytest.fail(f"Mount directory not writable: {e}")


class TestEndToEndIntegration:
    """End-to-end integration tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup for E2E tests"""
        if not REAL_ABS_API_TOKEN:
            pytest.skip("No API token available - create sample_files/abs_api.key.example with your token")
        
        self.server_url = REAL_ABS_SERVER_URL
        self.api_token = REAL_ABS_API_TOKEN
        self.library_id = get_library_id(self.server_url, self.api_token)
        
        if not self.library_id:
            pytest.skip("No libraries found on AudioBookshelf server. Please create a library first.")
    
    @pytest.mark.slow
    def test_complete_workflow_simulation(self):
        """Test a complete workflow: scan -> get books -> process recent"""
        # Step 1: Scan library
        scan_response = scan_library_for_books(
            self.server_url,
            self.library_id,
            self.api_token
        )
        
        # Scan might already be in progress, so accept 200 or 409
        assert scan_response.status_code in [200, 409]
        
        # Step 2: Get all books
        books_response = get_all_books(
            self.server_url,
            self.library_id,
            self.api_token
        )
        assert books_response.status_code == 200
        
        # Step 3: Get recent books (last 7 days)
        recent_books = get_audio_bookshelf_recent_books(
            books_response,
            days_ago=7
        )
        
        assert isinstance(recent_books, list)
        
        # If we have recent books, verify their structure
        if recent_books:
            for book in recent_books:
                assert "id" in book
                assert "media" in book
                assert "metadata" in book["media"]
                
                # Verify timestamp is within expected range (last 7 days)
                added_at = book["addedAt"]
                added_date = datetime.fromtimestamp(added_at / 1000, timezone.utc)
                seven_days_ago = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=7)
                assert added_date >= seven_days_ago, f"Book {book['media']['metadata']['title']} is older than 7 days"
        
        print(f"Integration test completed successfully:")
        print(f"- Server: {self.server_url}")
        print(f"- Library ID: {self.library_id}")
        print(f"- Total books in library: {len(books_response.json()['results'])}")
        print(f"- Recent books (7 days): {len(recent_books)}")


# Test runner configuration
if __name__ == "__main__":
    # Can be run directly for debugging
    pytest.main([__file__, "-v", "--tb=short"])
