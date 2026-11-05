module cryptography_sym_sha_sha256_msg_schedule (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_block,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [5:0]   out_index,
    output logic [31:0]  out_word
);

    logic        busy;
    logic [5:0]  out_ptr;
    logic [31:0] schedule_mem [0:63];

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    function automatic logic [31:0] rotr32(input logic [31:0] x, input logic [4:0] n);
        logic [31:0] right_part;
        logic [31:0] left_part;
    begin
        right_part = x >> n;
        left_part  = x << (5'd32 - n);
        rotr32     = right_part | left_part;
    end
    endfunction

    function automatic logic [31:0] sigma0(input logic [31:0] x);
        logic [31:0] r7;
        logic [31:0] r18;
        logic [31:0] s3;
    begin
        r7     = rotr32(x, 5'd7);
        r18    = rotr32(x, 5'd18);
        s3     = x >> 3;
        sigma0 = r7 ^ r18 ^ s3;
    end
    endfunction

    function automatic logic [31:0] sigma1(input logic [31:0] x);
        logic [31:0] r17;
        logic [31:0] r19;
        logic [31:0] s10;
    begin
        r17    = rotr32(x, 5'd17);
        r19    = rotr32(x, 5'd19);
        s10    = x >> 10;
        sigma1 = r17 ^ r19 ^ s10;
    end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (out_ptr == 6'd63);

    assign out_valid = busy;
    assign in_ready  = ~busy | frame_complete;
    assign out_index = busy ? out_ptr : 6'd0;
    assign out_word  = busy ? schedule_mem[out_ptr] : 32'd0;

    integer i;
    logic [31:0] temp_w [0:63];
    logic [31:0] sum_tmp;
    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_ptr <= 6'd0;
            for (i = 0; i < 64; i = i + 1) begin
                schedule_mem[i] <= 32'd0;
            end
        end else if (input_accepted) begin
            for (i = 0; i < 16; i = i + 1) begin
                temp_w[i] = in_block[511 - 32*i -: 32];
            end
            for (i = 16; i < 64; i = i + 1) begin
                sum_tmp = sigma1(temp_w[i - 2]) + temp_w[i - 7] + sigma0(temp_w[i - 15]) + temp_w[i - 16];
                temp_w[i] = sum_tmp;
            end
            for (i = 0; i < 64; i = i + 1) begin
                schedule_mem[i] <= temp_w[i];
            end
            busy    <= 1'b1;
            out_ptr <= 6'd0;
        end else if (output_consumed) begin
            if (out_ptr == 6'd63) begin
                busy    <= 1'b0;
                out_ptr <= 6'd0;
            end else begin
                busy    <= 1'b1;
                out_ptr <= out_ptr + 6'd1;
            end
        end
    end

endmodule