module data_systems_hash_murmur3_32_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_h,
    input  logic [31:0] in_k,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_h
);

    logic        busy;
    logic [31:0] out_h_next;

    wire input_accepted;
    wire output_consumed;

    logic [31:0] k1;
    logic [31:0] k2;
    logic [31:0] k3;
    logic [31:0] h1;
    logic [31:0] h2;
    logic [31:0] h3;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
    assign out_valid = rst ? 1'b0 : busy;

    always_comb begin
        k1 = in_k * 32'hcc9e2d51;
        k2 = (k1 << 15) | (k1 >> 17);
        k3 = k2 * 32'h1b873593;
        h1 = in_h ^ k3;
        h2 = (h1 << 13) | (h1 >> 19);
        h3 = (h2 * 32'h00000005) + 32'he6546b64;
        out_h_next = h3;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_h <= 32'h00000000;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_h <= out_h_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule