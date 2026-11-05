module simd_matrix_engine_top #(
    parameter int N = 8, parameter int SIMD_WIDTH = 4, parameter int DATA_WIDTH = 16
) (
    input logic clk, rst, start,
    input logic [2:0] op_select,
    input logic [N*N*DATA_WIDTH-1:0] mat_a_flat, mat_b_flat,
    output logic [N*N*DATA_WIDTH-1:0] mat_result_flat,
    output logic done
);
    logic [1:0] state;
    integer row_index,col_index;
    logic [SIMD_WIDTH*DATA_WIDTH-1:0] a_chunk,b_chunk,result_chunk;
    logic [SIMD_WIDTH-1:0] valid_lanes;
    wire lanes_ready;
    simd_datapath #(.SIMD_WIDTH(SIMD_WIDTH),.DATA_WIDTH(DATA_WIDTH)) datapath(
        .a_flat(a_chunk),.b_flat(b_chunk),.valid_lanes(valid_lanes),
        .op_sel(op_select),.result_flat(result_chunk),.ready(lanes_ready));
    always_ff @(posedge clk) begin
        if(rst) begin
            state<=0; row_index<=0; col_index<=0; a_chunk<=0; b_chunk<=0;
            valid_lanes<=0; mat_result_flat<=0; done<=0;
        end else begin
            done<=0;
            case(state)
                0: if(start) begin row_index<=0; col_index<=0; state<=1; end
                1: begin
                    for(int i=0;i<SIMD_WIDTH;i++) begin
                        valid_lanes[i]<=col_index+i<N;
                        a_chunk[i*DATA_WIDTH+:DATA_WIDTH]<='0;
                        b_chunk[i*DATA_WIDTH+:DATA_WIDTH]<='0;
                        if(col_index+i<N) begin
                            a_chunk[i*DATA_WIDTH+:DATA_WIDTH]<=mat_a_flat[(row_index*N+col_index+i)*DATA_WIDTH+:DATA_WIDTH];
                            b_chunk[i*DATA_WIDTH+:DATA_WIDTH]<=mat_b_flat[(row_index*N+col_index+i)*DATA_WIDTH+:DATA_WIDTH];
                        end
                    end
                    state<=2;
                end
                2: if(lanes_ready) begin
                    for(int i=0;i<SIMD_WIDTH;i++)
                        if(col_index+i<N)
                            mat_result_flat[(row_index*N+col_index+i)*DATA_WIDTH+:DATA_WIDTH]<=result_chunk[i*DATA_WIDTH+:DATA_WIDTH];
                    if(col_index+SIMD_WIDTH<N) begin col_index<=col_index+SIMD_WIDTH; state<=1; end
                    else if(row_index+1<N) begin row_index<=row_index+1; col_index<=0; state<=1; end
                    else state<=3;
                end
                3: begin done<=1; state<=0; end
                default: state<=0;
            endcase
        end
    end
endmodule

module simd_lane #(parameter int DATA_WIDTH = 16)(
    input logic [DATA_WIDTH-1:0] a,b,
    input logic [2:0] op_sel, input logic valid,
    output logic [DATA_WIDTH-1:0] result, output logic ready);
    always_comb begin
        result='0; ready=1;
        if(valid) case(op_sel)
            0: result=a+b;
            1: result=a-b;
            2: result=a*b;
            default: result='0;
        endcase
    end
endmodule
module simd_datapath #(parameter int SIMD_WIDTH = 4, parameter int DATA_WIDTH = 16)(
    input logic [SIMD_WIDTH*DATA_WIDTH-1:0] a_flat,b_flat,
    input logic [SIMD_WIDTH-1:0] valid_lanes, input logic [2:0] op_sel,
    output logic [SIMD_WIDTH*DATA_WIDTH-1:0] result_flat, output logic ready);
    wire [SIMD_WIDTH-1:0] lane_ready;
    for(genvar i=0;i<SIMD_WIDTH;i++) begin: lanes
        simd_lane #(.DATA_WIDTH(DATA_WIDTH)) lane(
            .a(a_flat[i*DATA_WIDTH+:DATA_WIDTH]),.b(b_flat[i*DATA_WIDTH+:DATA_WIDTH]),
            .op_sel(op_sel),.valid(valid_lanes[i]),
            .result(result_flat[i*DATA_WIDTH+:DATA_WIDTH]),.ready(lane_ready[i]));
    end
    assign ready=&lane_ready;
endmodule
