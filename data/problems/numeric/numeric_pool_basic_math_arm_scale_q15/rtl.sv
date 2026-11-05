module q15_vector_scale_element (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,
    input  logic [15:0] in_scale_fract,
    input  logic [7:0]  in_shift,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [15:0] sat16_from_signed64(input logic signed [63:0] value);
    begin
        if (value > 64'sd32767) begin
            sat16_from_signed64 = 16'h7FFF;
        end else if (value < -64'sd32768) begin
            sat16_from_signed64 = 16'h8000;
        end else begin
            sat16_from_signed64 = value[15:0];
        end
    end
endfunction

logic signed [15:0] sample_s;
logic signed [15:0] scale_s;
logic signed [7:0]  shift_s;
logic signed [31:0] product_s;
logic signed [8:0]  k_shift_s;
logic signed [63:0] product_ext_s;
logic signed [63:0] shifted_s;
logic [7:0]         left_amt;

always_comb begin
    sample_s      = in_sample;
    scale_s       = in_scale_fract;
    shift_s       = in_shift;
    product_s     = sample_s * scale_s;
    k_shift_s     = 9'sd15 - shift_s;
    product_ext_s = {{32{product_s[31]}}, product_s};
    shifted_s     = product_ext_s;
    left_amt      = 8'd0;

    if (k_shift_s >= 0) begin
        if (k_shift_s >= 9'sd63) begin
            if (product_s[31]) begin
                shifted_s = -64'sd1;
            end else begin
                shifted_s = 64'sd0;
            end
        end else begin
            shifted_s = product_ext_s >>> k_shift_s[5:0];
        end
    end else begin
        left_amt = 8'( -k_shift_s );
        if (left_amt >= 8'd32) begin
            if (product_s == 32'sd0) begin
                shifted_s = 64'sd0;
            end else if (product_s > 0) begin
                shifted_s = 64'sd32768;
            end else begin
                shifted_s = -64'sd32769;
            end
        end else begin
            shifted_s = product_ext_s <<< left_amt[5:0];
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_sample <= 16'h0000;
    end else if (input_accepted) begin
        out_sample <= sat16_from_signed64(shifted_s);
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule