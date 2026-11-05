module sorting_engine #(parameter int N = 8, parameter int WIDTH = 8) (
    input logic clk, rst, start,
    input logic [N*WIDTH-1:0] in_data,
    output logic done,
    output logic [N*WIDTH-1:0] out_data
);
    localparam int TOTAL=N*(N-1);
    localparam int CW=(TOTAL<2) ? 1 : $clog2(TOTAL+1);
    localparam int JW=(N<2) ? 1 : $clog2(N);
    localparam logic [1:0] IDLE=0, SORTING=1, DONE=2;
    logic [1:0] state;
    logic [WIDTH-1:0] work [0:N-1];
    logic [CW-1:0] left;
    logic [JW-1:0] j;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state<=IDLE; done<=0; out_data<=0; left<=0; j<=0;
            for (int i=0;i<N;i++) work[i]<=0;
        end else begin
            done<=0;
            case (state)
                IDLE: if (start) begin
                    for (int i=0;i<N;i++) work[i]<=in_data[i*WIDTH+:WIDTH];
                    left<=CW'(TOTAL); j<=0; state<=(N==1) ? DONE : SORTING;
                end
                SORTING: begin
                    if (work[j]>work[j+1]) begin
                        work[j]<=work[j+1]; work[j+1]<=work[j];
                    end
                    j<=(j==N-2) ? JW'(0) : j+1'b1;
                    left<=left-1'b1;
                    if (left==1) state<=DONE;
                end
                DONE: begin
                    for (int i=0;i<N;i++) out_data[i*WIDTH+:WIDTH]<=work[i];
                    done<=1; state<=IDLE;
                end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
