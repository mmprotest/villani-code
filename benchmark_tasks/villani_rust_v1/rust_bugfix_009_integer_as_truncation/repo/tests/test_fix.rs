use byte_convert::to_byte;

#[test]
fn value_100_converts_to_ok() {
    assert_eq!(to_byte(100), Ok(100u8));
}

#[test]
fn value_0_converts_to_ok() {
    assert_eq!(to_byte(0), Ok(0u8));
}

#[test]
fn value_255_converts_to_ok() {
    assert_eq!(to_byte(255), Ok(255u8));
}

#[test]
fn value_256_returns_err() {
    assert!(
        to_byte(256).is_err(),
        "256 is out of u8 range and should return Err"
    );
}

#[test]
fn value_1000_returns_err() {
    assert!(
        to_byte(1000).is_err(),
        "1000 is out of u8 range and should return Err"
    );
}

#[test]
fn out_of_range_does_not_silently_truncate() {
    let result = to_byte(256);
    assert!(result != Ok(0u8), "256 should not silently become 0");
}
