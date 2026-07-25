"""
Integration tests based on actual library structure.

Tests the series folder matching logic against real-world data patterns
found in the actual library to ensure consistency.
"""

from openaudible_to_audiobookshelf.utils import find_existing_series_folder


class TestRealLibraryPatterns:
    """Test patterns found in the actual book_tree.out library structure."""
    
    def test_everybody_loves_large_chests_duplicate_folders(self, tmp_path):
        """
        Test the exact issue from book_tree.out lines 183-196.
        
        Real scenario:
        - Neven_Iliev/EverybodyLovesLargeChests/ exists with volumes 1-10
        - Volume 11 gets placed in Neven_Iliev/Everybody_Loves_Large_Chests/
        
        This test ensures Volume 11 finds the existing folder.
        """
        # Setup existing structure (volumes 1-10)
        author_dir = tmp_path / "Neven_Iliev"
        existing_series = author_dir / "EverybodyLovesLargeChests"
        existing_series.mkdir(parents=True)
        
        # Create some existing books
        (existing_series / "Fizzlesprocket_Everybody_Loves_Large_Chests__Vol._2").mkdir()
        (existing_series / "Goroth__Everybody_Loves_Large_Chests_Book_7").mkdir()
        (existing_series / "Law__Everybody_Loves_Large_Chests_Vol._10").mkdir()
        
        # Volume 11 arrives with spaces in series name (from Audible metadata)
        result = find_existing_series_folder(
            "Neven_Iliev",
            "Everybody Loves Large Chests",  # spaces
            str(tmp_path)
        )
        
        assert result == "EverybodyLovesLargeChests"
        assert result != "Everybody_Loves_Large_Chests"
    
    def test_firefly_series_multiple_authors(self, tmp_path):
        """
        Test Firefly series appearing under different authors.
        
        From book_tree.out:
        - James_Lovegrove/Firefly_Series/
        - M._K._England/Firefly_Series/
        - Rosiee_Thor/Firefly_Series/
        
        Each author should have their own separate series folder (function only
        checks within the author's directory).
        """
        # Setup first author with existing series
        james_dir = tmp_path / "James_Lovegrove"
        james_series = james_dir / "FireflySeries"
        james_series.mkdir(parents=True)
        
        # James's new book should find his existing folder
        result1 = find_existing_series_folder(
            "James_Lovegrove",
            "Firefly Series",
            str(tmp_path)
        )
        assert result1 == "FireflySeries"
        
        # Second author has no existing folder, so creates new one
        result2 = find_existing_series_folder(
            "M._K._England",
            "Firefly Series",
            str(tmp_path)
        )
        # Creates same name but under different author
        assert result2 == "Firefly_Series"
    
    def test_complex_series_names_with_punctuation(self, tmp_path):
        """
        Test series names with various punctuation.
        
        Based on patterns like "Spells, Swords & Stealth" from book_tree.out.
        """
        author_dir = tmp_path / "Drew_Hayes"
        existing = author_dir / "SpellsSwordsStealth"
        existing.mkdir(parents=True)
        
        # Various ways Audible might format this
        patterns = [
            "Spells, Swords & Stealth",
            "Spells-Swords-and-Stealth",
            "Spells Swords and Stealth",
        ]
        
        for pattern in patterns:
            result = find_existing_series_folder(
                "Drew_Hayes",
                pattern,
                str(tmp_path)
            )
            # Should find existing folder (after normalization)
            assert "Spells" in result and "Swords" in result and "Stealth" in result
    
    def test_author_with_multiple_series(self, tmp_path):
        """
        Test author with multiple series doesn't mix them.
        
        Based on Eric_Ugland having The_Bad_Guys, The_Good_Guys, The_Grim_Guys.
        """
        author_dir = tmp_path / "Eric_Ugland"
        
        # Create existing series
        bad_guys = author_dir / "TheBadGuys"
        good_guys = author_dir / "TheGoodGuys"
        grim_guys = author_dir / "TheGrimGuys"
        
        bad_guys.mkdir(parents=True)
        good_guys.mkdir(parents=True)
        grim_guys.mkdir(parents=True)
        
        # New book for Bad Guys
        result = find_existing_series_folder(
            "Eric_Ugland",
            "The Bad Guys",
            str(tmp_path)
        )
        assert result == "TheBadGuys"
        assert result != "TheGoodGuys"
        assert result != "TheGrimGuys"
    
class TestEdgeCases:
    """Test edge cases discovered from real library patterns."""
    
    def test_series_name_with_periods(self, tmp_path):
        """Test series names with periods like 'Magic 2.0'."""
        author_dir = tmp_path / "Scott_Meyer"
        existing = author_dir / "Magic_2.0"
        existing.mkdir(parents=True)
        
        result = find_existing_series_folder(
            "Scott_Meyer",
            "Magic 2.0",
            str(tmp_path)
        )
        
        assert result == "Magic_2.0"
    
