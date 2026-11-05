module PoseidonMixT3 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [255:0] in_0,
    input logic [255:0] in_1,
    input logic [255:0] in_2,
    input logic [1:0] in_s,
    output logic out_valid,
    input logic out_ready,
    output logic [255:0] out,
    output logic out_error
);
    logic pending;
    logic [255:0] out_next;
    logic out_error_next;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    localparam logic [255:0] P = 256'd21888242871839275222246405745257275088548364400416034343698204186575808495617;
    logic [255:0] a, b, c, k0, k1, k2;
    logic [513:0] sum;

    always_comb begin
        a = in_0 % P; b = in_1 % P; c = in_2 % P;
        k0 = '0; k1 = '0; k2 = '0;
        case (in_s)
            2'd0: begin k0=256'h109b7f411ba0e4c9b2b70caf5c36a7b194be7c11ad24378bfedb68592ba8118b; k1=256'h16ed41e13bb9c0c66ae119424fddbcbc9314dc9fdbdeea55d6c64543dc4903e0; k2=256'h2b90bba00fca0589f617e7dcbfe82e0df706ab640ceb247b791a93b74e36736d; end
            2'd1: begin k0=256'h2969f27eed31a480b9c36c764379dbca2cc8fdd1415c3dded62940bcde0bd771; k1=256'h2e2419f9ec02ec394c9871c832963dc1b89d743c8c7b964029b2311687b1fe23; k2=256'h101071f0032379b697315876690f053d148d4e109f5fb065c8aacc55a0f89bfa; end
            2'd2: begin k0=256'h143021ec686a3f330d5f9e654638065ce6cd79e28c5b3753326244ee65a1b1a7; k1=256'h176cc029695ad02582a70eff08a6fd99d057e12e58e7d7b6b16cdfabc8ee2911; k2=256'h19a3fc0a56702bf417ba7fee3802593fa644470307043f7773279cd71d25d5e0; end
            default: begin end
        endcase
        sum = 514'(a)*k0 + 514'(b)*k1 + 514'(c)*k2;
        out_error_next = in_s == 3;
        out_next = 256'(sum % P);
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            out <= '0;
            out_error <= '0;
        end else if (in_ready) begin
            pending <= in_valid;
            if (in_valid) begin
                out <= out_next;
                out_error <= out_error_next;
            end
        end
    end
endmodule
