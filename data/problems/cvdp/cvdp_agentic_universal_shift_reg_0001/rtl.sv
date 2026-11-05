module universal_shift_register #(parameter int N = 8) (
    input  logic clk,
    input  logic rst,
    input  logic [1:0] mode_sel,
    input  logic shift_dir,
    input  logic serial_in,
    input  logic [N-1:0] parallel_in,
    output logic [N-1:0] q,
    output wire serial_out
);
    assign serial_out = shift_dir ? q[N-1] : q[0];
    always_ff @(posedge clk or posedge rst) begin
        if (rst) q <= '0;
        else case (mode_sel)
            2'b00: q <= q;
            2'b01: q <= shift_dir ? (q << 1) | N'(serial_in)
                                 : (q >> 1) | (N'(serial_in) << (N-1));
            2'b10: q <= shift_dir ? (q << 1) | N'(q[N-1])
                                 : (q >> 1) | (N'(q[0]) << (N-1));
            2'b11: q <= parallel_in;
        endcase
    end
endmodule
