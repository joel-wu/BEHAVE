module popcount32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_data,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [5:0]  out_popcount
);

logic busy;
wire  input_accepted;
wire  output_consumed;

function automatic logic [5:0] popcount32_fn(input logic [31:0] value);
    logic [5:0] count;
    begin
        count = 6'd0;
        count = count + value[0];
        count = count + value[1];
        count = count + value[2];
        count = count + value[3];
        count = count + value[4];
        count = count + value[5];
        count = count + value[6];
        count = count + value[7];
        count = count + value[8];
        count = count + value[9];
        count = count + value[10];
        count = count + value[11];
        count = count + value[12];
        count = count + value[13];
        count = count + value[14];
        count = count + value[15];
        count = count + value[16];
        count = count + value[17];
        count = count + value[18];
        count = count + value[19];
        count = count + value[20];
        count = count + value[21];
        count = count + value[22];
        count = count + value[23];
        count = count + value[24];
        count = count + value[25];
        count = count + value[26];
        count = count + value[27];
        count = count + value[28];
        count = count + value[29];
        count = count + value[30];
        count = count + value[31];
        popcount32_fn = count;
    end
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy         <= 1'b0;
        out_popcount <= 6'd0;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_popcount <= popcount32_fn(in_data);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule