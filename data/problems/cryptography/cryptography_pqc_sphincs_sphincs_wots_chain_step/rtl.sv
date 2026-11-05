module cryptography_pqc_sphincs_sphincs_wots_chain_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [6:0]   in_chain_idx,
    input  logic [255:0] in_msg,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [3:0]   out_chain_step
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [9:0]  csum;
logic [3:0]  selected_digit;
logic [7:0]  selected_byte;
logic [4:0]  byte_idx;
logic [3:0]  hi_nibble;
logic [3:0]  lo_nibble;
logic [13:0] csum_aligned_tmp;
logic [15:0] csum_aligned;
logic [7:0]  c0;
logic [7:0]  c1;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    csum           = 10'd0;
    selected_digit = 4'b0000;
    selected_byte  = 8'b0000_0000;
    byte_idx       = 5'd0;
    hi_nibble      = 4'b0000;
    lo_nibble      = 4'b0000;
    csum_aligned_tmp = 14'd0;
    csum_aligned   = 16'd0;
    c0             = 8'd0;
    c1             = 8'd0;

    for (int k = 0; k < 32; k = k + 1) begin
        selected_byte = in_msg[255 - 8*k -: 8];
        hi_nibble = selected_byte[7:4];
        lo_nibble = selected_byte[3:0];
        csum = csum + (10'd15 - {6'b0, hi_nibble}) + (10'd15 - {6'b0, lo_nibble});
    end

    csum_aligned_tmp = {csum, 4'b0000};
    csum_aligned = {2'b00, csum_aligned_tmp};
    c0 = csum_aligned[15:8];
    c1 = csum_aligned[7:0];

    if (in_chain_idx <= 7'd63) begin
        byte_idx = in_chain_idx[5:1];
        selected_byte = in_msg[255 - 8*byte_idx -: 8];
        if (in_chain_idx[0] == 1'b0) begin
            selected_digit = selected_byte[7:4];
        end else begin
            selected_digit = selected_byte[3:0];
        end
    end else if (in_chain_idx == 7'd64) begin
        selected_digit = c0[7:4];
    end else if (in_chain_idx == 7'd65) begin
        selected_digit = c0[3:0];
    end else if (in_chain_idx == 7'd66) begin
        selected_digit = c1[7:4];
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy           <= 1'b0;
        out_chain_step <= 4'b0000;
    end else if (input_accepted) begin
        busy           <= 1'b1;
        out_chain_step <= selected_digit;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule