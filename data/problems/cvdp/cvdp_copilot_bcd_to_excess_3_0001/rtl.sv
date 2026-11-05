module bcd_to_excess_3 (
    input logic [3:0] bcd,
    output logic [3:0] excess3,
    output logic error
);
    assign error=bcd>9;
    assign excess3=error ? 4'b0 : bcd+4'd3;
endmodule
