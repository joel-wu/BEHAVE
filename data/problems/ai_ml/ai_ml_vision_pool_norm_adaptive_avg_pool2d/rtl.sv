module ai_ml_vision_pool_norm_adaptive_avg_pool2d (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_h,
    input  logic [3:0]   in_w,
    input  logic [3:0]   out_h,
    input  logic [3:0]   out_w,
    input  logic [255:0] in_data,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_data
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [3:0] clamp_1_4(input logic [3:0] v);
    begin
        if (v < 4'd1) begin
            clamp_1_4 = 4'd1;
        end else if (v > 4'd4) begin
            clamp_1_4 = 4'd4;
        end else begin
            clamp_1_4 = v;
        end
    end
endfunction

function automatic logic signed [15:0] floor_div_pos_den(
    input logic signed [31:0] num,
    input logic [4:0] den
);
    logic signed [31:0] den_s;
    logic signed [31:0] q;
    logic signed [31:0] prod;
    logic signed [15:0] q16;
    begin
        den_s = $signed({27'd0, den});
        q     = num / den_s;
        prod  = q * den_s;
        q16   = q[15:0];
        if ((num < 0) && (num != prod)) begin
            floor_div_pos_den = q16 - 16'sd1;
        end else begin
            floor_div_pos_den = q16;
        end
    end
endfunction

logic [255:0] out_data_next;
logic [3:0] h_in_c, w_in_c, h_out_c, w_out_c;
logic [31:0] h_in_u32, w_in_u32, h_out_u32, w_out_u32;

integer orow, ocol, r, c;
integer idx_out;
integer idx_in;
integer h_start_i, h_end_i, w_start_i, w_end_i;
integer count_i;
logic signed [31:0] total_s;
logic signed [15:0] y_s;
logic signed [15:0] sample_s;
logic signed [31:0] sample_ext_s;

always_comb begin
    h_in_c   = clamp_1_4(in_h);
    w_in_c   = clamp_1_4(in_w);
    h_out_c  = clamp_1_4(out_h);
    w_out_c  = clamp_1_4(out_w);
    h_in_u32  = {28'd0, h_in_c};
    w_in_u32  = {28'd0, w_in_c};
    h_out_u32 = {28'd0, h_out_c};
    w_out_u32 = {28'd0, w_out_c};

    out_data_next = 256'd0;
    idx_out = 0;
    idx_in = 0;
    h_start_i = 0;
    h_end_i = 0;
    w_start_i = 0;
    w_end_i = 0;
    count_i = 0;
    total_s = 32'sd0;
    y_s = 16'sd0;
    sample_s = 16'sd0;
    sample_ext_s = 32'sd0;

    for (orow = 0; orow < 4; orow = orow + 1) begin
        for (ocol = 0; ocol < 4; ocol = ocol + 1) begin
            y_s = 16'sd0;

            if ((orow < h_out_c) && (ocol < w_out_c)) begin
                h_start_i = (orow * h_in_u32) / h_out_u32;
                h_end_i   = (((orow + 1) * h_in_u32) + h_out_u32 - 1) / h_out_u32;
                w_start_i = (ocol * w_in_u32) / w_out_u32;
                w_end_i   = (((ocol + 1) * w_in_u32) + w_out_u32 - 1) / w_out_u32;

                total_s = 32'sd0;
                for (r = 0; r < 4; r = r + 1) begin
                    for (c = 0; c < 4; c = c + 1) begin
                        if ((h_start_i <= r) && (r < h_end_i) &&
                            (w_start_i <= c) && (c < w_end_i)) begin
                            idx_in = 4 * r + c;
                            sample_s = $signed(in_data[16*idx_in +: 16]);
                            sample_ext_s = {{16{sample_s[15]}}, sample_s};
                            total_s = total_s + sample_ext_s;
                        end
                    end
                end

                count_i = (h_end_i - h_start_i) * (w_end_i - w_start_i);
                y_s = floor_div_pos_den(total_s, count_i[4:0]);
            end

            idx_out = 4 * orow + ocol;
            out_data_next[16*idx_out +: 16] = y_s;
        end
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy     <= 1'b0;
        out_data <= 256'd0;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_data <= out_data_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule