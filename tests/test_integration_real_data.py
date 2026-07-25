"""Live integration tests for repo behavior against a real AudioBookShelf server."""

import pytest
import requests

from openaudible_to_audiobookshelf.audio_bookshelf import (
    get_all_books,
    scan_library_for_books,
)
from tests.test_config import (
    REAL_ABS_SERVER_URL, 
    REAL_ABS_API_TOKEN, 
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


class TestRealAudioBookshelfIntegration:
    """Integration tests that exercise repo code against a live ABS server."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Skip cleanly when live ABS credentials or libraries are unavailable."""
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
    
    def test_get_all_books_real(self):
        """get_all_books should return the expected ABS response shape."""
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
        """scan_library_for_books should trigger a live scan request successfully."""
        response = scan_library_for_books(
            self.server_url,
            self.library_id,
            self.api_token
        )
        
        # Scan can return 200 (started) or other codes depending on server state
        assert response.status_code in [200, 409], f"Unexpected scan response: {response.status_code}"


# Test runner configuration
if __name__ == "__main__":
    # Can be run directly for debugging
    pytest.main([__file__, "-v", "--tb=short"])
