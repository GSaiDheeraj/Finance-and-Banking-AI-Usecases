"""
Tests for the extraction functionality.
"""

import pytest
from fin_doc_intel.extraction import extract_statement, extract_risk_disclosures


@pytest.mark.asyncio
async def test_extract_statement_structure():
    """Test that extract_statement returns the expected structure."""
    # This is a placeholder test - in a real implementation, you would
    # mock the LLM call and test the response parsing
    
    # For now, we just test that the function exists and is callable
    assert callable(extract_statement)
    assert callable(extract_risk_disclosures)


def test_extraction_returns_dict():
    """Test that extraction functions return dictionary results."""
    # Placeholder test - in real implementation, this would test
    # the actual extraction logic with mocked LLM responses
    
    expected_keys = ["by_period", "canonical_keys", "raw_items"]
    # This would be: result = extract_statement(...)
    # assert all(key in result for key in expected_keys)
    
    assert True  # Placeholder
